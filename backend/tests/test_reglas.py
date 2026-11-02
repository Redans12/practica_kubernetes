"""Reglas 1, 2 y 3, lista de espera/reasignación, check-in, tareas programadas y máquina de estados."""
import itertools
import uuid
from datetime import timedelta
from decimal import Decimal

import jwt
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import ESTADOS_RESERVA, ListaEspera, OutboxEvento, Pago, Reserva, Usuario
from app.servicios import reservas as srv
from app.servicios.errores import ReglaDeNegocio
from tests.conftest import a_utc, iso


# ---------------------------------------------------------------- máquina de estados
@pytest.mark.parametrize("de,a", [p for p in itertools.product(ESTADOS_RESERVA, ESTADOS_RESERVA) if p not in srv.TRANSICIONES])
def test_transicion_no_permitida_se_rechaza(de, a):
    with pytest.raises(ReglaDeNegocio):
        srv.cambiar_estado(None, Reserva(estado=de), a)


@pytest.mark.parametrize("de,a", sorted(srv.TRANSICIONES))
def test_transicion_permitida_cambia_estado_y_emite_evento(db, ana, cancha, reloj, reservar, de, a):
    rid = reservar(ana, cancha).json()["id"]
    r = db.get(Reserva, uuid.UUID(rid))
    r.estado = de
    antes = len(db.scalars(select(OutboxEvento)).all())
    srv.cambiar_estado(db, r, a)
    db.commit()
    assert r.estado == a
    assert len(db.scalars(select(OutboxEvento)).all()) == antes + 1


# ---------------------------------------------------------------- regla 1: cancelación progresiva
@pytest.mark.parametrize("horas,penal,bloquea", [
    (5, "0.00", False), (4, "0.00", False),            # 4 h o más: sin cargo
    (3.9, "50.00", False), (3, "50.00", False), (1, "50.00", False),   # menos de 4 h: 50 % del depósito
    (0.99, "100.00", True), (0.5, "100.00", True),     # menos de 1 h: 100 % y bloqueo de horario estelar
])
def test_regla1_penalizacion_segun_anticipacion(client, db, ana, cancha, reloj, confirmada, horas, penal, bloquea):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    inicio = a_utc(client.get(f"/api/reservas/{rid}", headers=ana.h).json()["inicio"])
    reloj.fijar(inicio - timedelta(hours=horas))
    r = client.post(f"/api/reservas/{rid}/cancelar", headers=ana.h)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["reserva"]["estado"] == "liberada_espera"
    assert Decimal(cuerpo["penalizacion"]) == Decimal(penal)
    assert Decimal(cuerpo["reembolso"]) == Decimal("100.00") - Decimal(penal)
    usuario = db.get(Usuario, uuid.UUID(ana.id))
    db.refresh(usuario)
    assert (usuario.bloqueado_hasta == reloj.ahora() + timedelta(days=7)) if bloquea else usuario.bloqueado_hasta is None
    cobros = [p for p in db.scalars(select(Pago)) if p.tipo == "penalizacion"]
    assert (len(cobros) == 1 and cobros[0].monto == Decimal(penal)) if Decimal(penal) > 0 else not cobros


def test_regla1_bloqueo_estelar_dura_7_dias_y_solo_aplica_al_horario_estelar(client, ana, cancha, reloj, confirmada, reservar):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(minutes=30))
    client.post(f"/api/reservas/{rid}/cancelar", headers=ana.h)
    estelar = reservar(ana, cancha, dia=2, hora=19)                       # martes... miércoles 19:00
    assert estelar.status_code == 403 and "estelar" in estelar.json()["detail"]
    assert reservar(ana, cancha, dia=2, hora=10).status_code == 201       # fuera de horario estelar: permitido
    assert reservar(ana, cancha, dia=5, hora=19).status_code == 201       # sábado 19:00: no es lunes a viernes
    reloj.avanzar(days=7, minutes=1)                                      # se cumplió el bloqueo
    assert reservar(ana, cancha, dia=9, hora=19).status_code == 201       # martes 11-nov 19:00


@pytest.mark.parametrize("dia,hora,esperado", [
    (1, 18, 403), (1, 21, 403), (1, 22, 201), (1, 17, 201),               # martes: estelar es 18:00 a 21:59
    (5, 19, 201), (6, 19, 201),                                           # sábado y domingo no son estelares
])
def test_horario_estelar_limites(client, db, ana, cancha, reloj, reservar, dia, hora, esperado):
    db.execute(Usuario.__table__.update().values(bloqueado_hasta=reloj.ahora() + timedelta(days=3)))
    db.commit()
    assert reservar(ana, cancha, dia=dia, hora=hora).status_code == esperado


def test_cancelar_borrador_sin_costo(client, db, ana, cancha, reloj, reservar):
    rid = reservar(ana, cancha).json()["id"]
    r = client.post(f"/api/reservas/{rid}/cancelar", headers=ana.h).json()
    assert r["reserva"]["estado"] == "expirada" and Decimal(r["penalizacion"]) == 0
    assert db.get(Usuario, uuid.UUID(ana.id)).bloqueado_hasta is None


def test_cancelacion_permisos_y_estados(client, ana, beto, admin, cancha, reloj, confirmada):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    assert client.post(f"/api/reservas/{rid}/cancelar", headers=beto.h).status_code == 404     # no la ve
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(minutes=20))
    por_admin = client.post(f"/api/reservas/{rid}/cancelar", headers=admin.h).json()
    assert Decimal(por_admin["penalizacion"]) == 0           # la cancela el club: el jugador no paga
    assert client.post(f"/api/reservas/{rid}/cancelar", headers=ana.h).status_code == 409    # ya liberada


def test_no_se_cancela_un_turno_que_ya_empezo(client, ana, cancha, reloj, confirmada):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    reloj.fijar(a_utc(iso(1, 10)) + timedelta(minutes=5))
    assert client.post(f"/api/reservas/{rid}/cancelar", headers=ana.h).status_code == 409


# ---------------------------------------------------------------- lista de espera (regla 4 al liberar)
def test_lista_de_espera_validaciones(client, db, ana, beto, cancha, reloj, reservar):
    cuerpo = {"cancha_id": cancha, "inicio": iso(1, 10), "horas": 1}
    assert client.post("/api/lista-espera", headers=beto.h, json=cuerpo).status_code == 409    # horario libre
    reservar(ana, cancha, dia=1, hora=10)
    assert client.post("/api/lista-espera", headers=beto.h, json=cuerpo).status_code == 201
    assert client.post("/api/lista-espera", headers=beto.h, json=cuerpo).status_code == 409    # ya estaba anotado
    assert client.post("/api/lista-espera", headers=beto.h, json={**cuerpo, "horas": 9}).status_code == 422
    assert client.post("/api/lista-espera", headers=beto.h, json={**cuerpo, "cancha_id": str(uuid.uuid4())}).status_code == 404
    assert len(client.get("/api/lista-espera", headers=beto.h).json()) == 1
    assert client.get("/api/lista-espera", headers=ana.h).json() == []


def test_salir_de_la_lista_de_espera(client, ana, beto, cancha, reloj, reservar):
    reservar(ana, cancha, dia=1, hora=10)
    eid = client.post("/api/lista-espera", headers=beto.h, json={"cancha_id": cancha, "inicio": iso(1, 10), "horas": 1}).json()["id"]
    assert client.delete(f"/api/lista-espera/{eid}", headers=ana.h).status_code == 404       # no es suya
    assert client.delete(f"/api/lista-espera/{eid}", headers=beto.h).status_code == 204
    assert client.get("/api/lista-espera", headers=beto.h).json() == []


def test_al_cancelar_se_reasigna_al_primero_de_la_fila(client, db, ana, beto, admin, crear_usuario, cancha, reloj, confirmada):
    carla = crear_usuario("carla@ejemplo.test")
    rid = confirmada(ana, cancha, dia=1, hora=10)
    cuerpo = {"cancha_id": cancha, "inicio": iso(1, 10), "horas": 1}
    client.post("/api/lista-espera", headers=beto.h, json=cuerpo)
    client.post("/api/lista-espera", headers=carla.h, json=cuerpo)

    r = client.post(f"/api/reservas/{rid}/cancelar", headers=ana.h).json()
    nueva_id = r["reasignada_a_reserva"]
    assert r["reserva"]["estado"] == "reasignada"                         # la original queda terminal
    nueva = client.get(f"/api/reservas/{nueva_id}", headers=beto.h).json()    # Beto llegó primero
    assert nueva["estado"] == "borrador" and nueva["usuario_id"] == beto.id
    assert client.get("/api/lista-espera", headers=beto.h).json() == []   # salió de la fila
    assert len(client.get("/api/lista-espera", headers=carla.h).json()) == 1
    assert client.post(f"/api/reservas/{nueva_id}/pagar", headers=beto.h).json()["estado"] == "confirmada"
    assert db.query(ListaEspera).count() == 1


def test_si_nadie_espera_el_turno_queda_liberado_y_se_puede_reservar(client, ana, beto, cancha, reloj, confirmada, reservar):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    assert client.post(f"/api/reservas/{rid}/cancelar", headers=ana.h).json()["reserva"]["estado"] == "liberada_espera"
    assert reservar(beto, cancha, dia=1, hora=10).status_code == 201


# ---------------------------------------------------------------- regla 2: quórum
@pytest.fixture
def partido(client, db, ana, cancha, reloj, reservar):
    """Partido abierto de pádel (cupo 4) para el martes 10:00 local, ya confirmado por el organizador."""
    rid = reservar(ana, cancha, dia=1, hora=10, es_partido_abierto=True).json()["id"]
    client.post(f"/api/reservas/{rid}/pagar", headers=ana.h)
    return rid


def _invitar(client, rid, dueno, *usuarios):
    for u in usuarios:
        assert client.post(f"/api/reservas/{rid}/jugadores", headers=dueno.h, json={"email": u.email}).status_code == 204


def test_regla2_sin_quorum_a_2_horas_se_libera(client, db, ana, beto, crear_usuario, partido, reloj):
    carla, dani = crear_usuario("carla@ejemplo.test"), crear_usuario("dani@ejemplo.test")
    _invitar(client, partido, ana, beto, carla, dani)
    client.post(f"/api/reservas/{partido}/asistencia", headers=beto.h)
    client.post(f"/api/reservas/{partido}/asistencia", headers=carla.h)       # Dani no confirma
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(hours=2))
    assert srv.revisar_quorum(db) == {"revisadas": 1, "liberadas": 1, "reasignadas": 0}
    assert client.get(f"/api/reservas/{partido}", headers=ana.h).json()["estado"] == "liberada_espera"
    ultimo = [e for e in db.scalars(select(OutboxEvento).order_by(OutboxEvento.creado_en))][-1]
    assert ultimo.payload["detalle"]["motivo"] == "quorum_incompleto"


def test_regla2_con_quorum_completo_se_conserva(client, db, ana, beto, crear_usuario, partido, reloj):
    carla, dani = crear_usuario("carla@ejemplo.test"), crear_usuario("dani@ejemplo.test")
    _invitar(client, partido, ana, beto, carla, dani)
    for u in (beto, carla, dani):
        client.post(f"/api/reservas/{partido}/asistencia", headers=u.h)
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(hours=2))
    assert srv.revisar_quorum(db)["liberadas"] == 0
    assert client.get(f"/api/reservas/{partido}", headers=ana.h).json()["estado"] == "confirmada"


def test_regla2_cupo_incompleto_aunque_todos_confirmen(client, db, ana, beto, partido, reloj):
    _invitar(client, partido, ana, beto)                                       # solo 2 de 4
    client.post(f"/api/reservas/{partido}/asistencia", headers=beto.h)
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(hours=2))
    assert srv.revisar_quorum(db)["liberadas"] == 1


def test_regla2_antes_de_las_2_horas_no_se_toca(client, db, ana, partido, reloj):
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(hours=2, minutes=1))
    assert srv.revisar_quorum(db)["revisadas"] == 0


def test_regla2_al_liberar_por_quorum_se_reasigna_a_quien_espera(client, db, ana, beto, partido, reloj):
    client.post("/api/lista-espera", headers=beto.h, json={"cancha_id": db.get(Reserva, uuid.UUID(partido)).cancha_id.__str__(),
                                                           "inicio": iso(1, 10), "horas": 1})
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(hours=2))
    assert srv.revisar_quorum(db) == {"revisadas": 1, "liberadas": 1, "reasignadas": 1}
    assert client.get(f"/api/reservas/{partido}", headers=ana.h).json()["estado"] == "reasignada"


def test_invitaciones_validaciones(client, db, ana, beto, crear_usuario, partido, cancha, reservar):
    assert client.post(f"/api/reservas/{partido}/jugadores", headers=beto.h, json={"email": "x@ejemplo.test"}).status_code == 404   # ni la ve
    assert client.post(f"/api/reservas/{partido}/jugadores", headers=ana.h, json={"email": "nadie@ejemplo.test"}).status_code == 404
    _invitar(client, partido, ana, beto)
    assert client.post(f"/api/reservas/{partido}/jugadores", headers=ana.h, json={"email": beto.email}).status_code == 409      # repetido
    assert client.post(f"/api/reservas/{partido}/jugadores", headers=beto.h, json={"email": ana.email}).status_code == 403      # invitado no invita
    extra = [crear_usuario(f"j{i}@ejemplo.test") for i in range(2)]
    _invitar(client, partido, ana, *extra)                                    # llega a 4
    assert client.post(f"/api/reservas/{partido}/jugadores", headers=ana.h, json={"email": "otro@ejemplo.test"}).status_code in (404, 409)
    privada = reservar(ana, cancha, dia=2, hora=10).json()["id"]
    assert client.post(f"/api/reservas/{privada}/jugadores", headers=ana.h, json={"email": beto.email}).status_code == 409


def test_cupo_de_pádel_son_4_jugadores(client, db, ana, crear_usuario, partido):
    for i in range(3):
        _invitar(client, partido, ana, crear_usuario(f"j{i}@ejemplo.test"))
    sobrante = crear_usuario("sobrante@ejemplo.test")
    r = client.post(f"/api/reservas/{partido}/jugadores", headers=ana.h, json={"email": sobrante.email})
    assert r.status_code == 409 and "cupo" in r.json()["detail"]


def test_confirmar_asistencia_solo_si_estas_invitado(client, ana, beto, partido):
    assert client.post(f"/api/reservas/{partido}/asistencia", headers=beto.h).status_code == 404
    _invitar(client, partido, ana, beto)
    assert client.post(f"/api/reservas/{partido}/asistencia", headers=beto.h).status_code == 204
    assert client.get(f"/api/reservas/{partido}", headers=beto.h).status_code == 200      # el invitado ya la ve


# ---------------------------------------------------------------- regla 3: luz
@pytest.mark.parametrize("minutos,codigo", [(-11, 403), (-10, 200), (-1, 200), (0, 200), (10, 200), (11, 403), (60, 403)])
def test_regla3_ventana_de_10_minutos(client, ana, cancha, reloj, confirmada, minutos, codigo):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    reloj.fijar(a_utc(iso(1, 10)) + timedelta(minutes=minutos))
    assert client.post(f"/api/reservas/{rid}/token-luz", headers=ana.h).status_code == codigo


def test_regla3_token_contenido_y_evento(client, db, ana, cancha, reloj, confirmada):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    reloj.fijar(a_utc(iso(1, 10)))
    r = client.post(f"/api/reservas/{rid}/token-luz", headers=ana.h).json()
    s = get_settings()
    datos = jwt.decode(r["token"], s.jwt_secret, algorithms=[s.jwt_algorithm], options={"verify_exp": False})
    assert datos["sub"] == rid and datos["scope"] == "luz" and a_utc(r["valido_hasta"]) == a_utc(iso(1, 11))
    assert any(e.tipo == "luz_token" for e in db.scalars(select(OutboxEvento)))


def test_regla3_solo_con_reserva_confirmada(client, ana, cancha, reloj, reservar, confirmada, admin):
    borrador = reservar(ana, cancha, dia=1, hora=12).json()["id"]
    reloj.fijar(a_utc(iso(1, 12)))
    assert client.post(f"/api/reservas/{borrador}/token-luz", headers=ana.h).status_code == 403
    rid = confirmada(ana, cancha, dia=1, hora=14)
    reloj.fijar(a_utc(iso(1, 14)))
    assert client.post(f"/api/reservas/{rid}/checkin", headers=admin.h).status_code == 200
    assert client.post(f"/api/reservas/{rid}/token-luz", headers=ana.h).status_code == 403    # ya está en juego


def test_regla3_quien_puede_pedirlo(client, ana, beto, admin, crear_usuario, cancha, reloj, partido):
    ajeno = crear_usuario("ajeno@ejemplo.test")
    _invitar(client, partido, ana, beto)
    reloj.fijar(a_utc(iso(1, 10)))
    assert client.post(f"/api/reservas/{partido}/token-luz", headers=ajeno.h).status_code == 404
    assert client.post(f"/api/reservas/{partido}/token-luz", headers=beto.h).status_code == 200     # jugador invitado
    assert client.post(f"/api/reservas/{partido}/token-luz", headers=admin.h).status_code == 200
    assert client.post(f"/api/reservas/{partido}/token-luz").status_code == 401


# ---------------------------------------------------------------- check-in, fin de turno y no-show
def test_checkin_solo_administrador_y_en_ventana(client, ana, admin, cancha, reloj, confirmada):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(minutes=5))
    assert client.post(f"/api/reservas/{rid}/checkin", headers=ana.h).status_code == 403
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(minutes=11))
    assert client.post(f"/api/reservas/{rid}/checkin", headers=admin.h).status_code == 403
    reloj.fijar(a_utc(iso(1, 10)) - timedelta(minutes=5))
    assert client.post(f"/api/reservas/{rid}/checkin", headers=admin.h).json()["estado"] == "en_juego"
    assert client.post(f"/api/reservas/{rid}/checkin", headers=admin.h).status_code == 409
    assert client.post(f"/api/reservas/{uuid.uuid4()}/checkin", headers=admin.h).status_code == 404


def test_checkin_de_un_borrador_se_rechaza(client, ana, admin, cancha, reloj, reservar):
    rid = reservar(ana, cancha, dia=1, hora=10).json()["id"]
    assert client.post(f"/api/reservas/{rid}/checkin", headers=admin.h).status_code == 409


def test_fin_del_turno_completa_y_suma_ranking(client, db, ana, beto, admin, cancha, reloj, partido):
    _invitar(client, partido, ana, beto)
    client.post(f"/api/reservas/{partido}/asistencia", headers=beto.h)
    reloj.fijar(a_utc(iso(1, 10)))
    client.post(f"/api/reservas/{partido}/checkin", headers=admin.h)
    antes = {u.email: u.ranking_puntos for u in db.scalars(select(Usuario))}
    reloj.fijar(a_utc(iso(1, 10)) + timedelta(minutes=59))
    assert srv.finalizar_turnos(db) == 0                                      # todavía no termina
    reloj.fijar(a_utc(iso(1, 11)))
    assert srv.finalizar_turnos(db) == 1
    db.expire_all()
    despues = {u.email: u.ranking_puntos for u in db.scalars(select(Usuario))}
    assert despues[ana.email] == antes[ana.email] + 10 and despues[beto.email] == antes[beto.email] + 10
    assert despues[admin.email] == antes[admin.email]
    assert client.get(f"/api/reservas/{partido}", headers=ana.h).json()["estado"] == "completada"
    assert srv.finalizar_turnos(db) == 0                                      # idempotente


def test_no_show_se_trata_como_cancelacion_de_ultimo_minuto(client, db, ana, cancha, reloj, confirmada):
    rid = confirmada(ana, cancha, dia=1, hora=10)
    reloj.fijar(a_utc(iso(1, 10)) + timedelta(minutes=14))
    assert srv.marcar_no_show(db) == 0
    reloj.fijar(a_utc(iso(1, 10)) + timedelta(minutes=15))
    assert srv.marcar_no_show(db) == 1
    r = client.get(f"/api/reservas/{rid}", headers=ana.h).json()
    assert r["estado"] == "liberada_espera" and Decimal(r["penalizacion"]) == 100
    db.expire_all()
    assert db.get(Usuario, uuid.UUID(ana.id)).bloqueado_hasta is not None


def test_un_jugador_invitado_no_puede_pagar_ni_cancelar_el_partido(client, ana, beto, partido):
    _invitar(client, partido, ana, beto)
    assert client.post(f"/api/reservas/{partido}/cancelar", headers=beto.h).status_code == 403
    assert client.post(f"/api/reservas/{partido}/pagar", headers=beto.h).status_code == 403
    assert client.get(f"/api/reservas/{partido}", headers=ana.h).json()["estado"] == "confirmada"
