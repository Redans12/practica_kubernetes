"""Reservas: creación, validación, regla 4 (un turno, una reserva), material, pago, expiración y consulta."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import Material, OutboxEvento, Pago, Reserva
from tests.conftest import a_utc, iso


def test_crear_reserva_queda_en_borrador_con_expiracion_y_evento(client, db, ana, cancha, reloj, reservar):
    r = reservar(ana, cancha, dia=1, hora=10, horas=2)
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["estado"] == "borrador" and Decimal_(cuerpo["deposito"]) == 100
    assert a_utc(cuerpo["expira_en"]) == reloj.ahora() + timedelta(minutes=10)
    assert a_utc(cuerpo["fin"]) - a_utc(cuerpo["inicio"]) == timedelta(hours=2)
    ev = db.scalars(select(OutboxEvento)).all()
    assert len(ev) == 1 and ev[0].payload["a_estado"] == "borrador" and ev[0].procesado_en is None


def Decimal_(x):
    from decimal import Decimal
    return Decimal(x)


@pytest.mark.parametrize("campos,codigo", [
    ({"inicio": "2026-11-01T10:00:00-06:00"}, 422),     # en el pasado
    ({"inicio": "2026-11-03T10:30:00-06:00"}, 422),     # no empieza en punto
    ({"inicio": "2026-11-03T10:00:00"}, 422),           # sin zona horaria
    ({"inicio": "2027-02-01T10:00:00-06:00"}, 422),     # más de 60 días
    ({"inicio": "no-es-fecha"}, 422),
    ({"horas": 0}, 422), ({"horas": 4}, 422), ({"horas": "dos"}, 422),
    ({"campo_extra": 1}, 422),
    ({"materiales": [{"material_id": str(uuid.uuid4()), "cantidad": 0}]}, 422),
])
def test_reserva_datos_invalidos(client, ana, cancha, reloj, campos, codigo):
    base = {"cancha_id": cancha, "inicio": iso(1, 10), "horas": 1}
    assert client.post("/api/reservas", headers=ana.h, json={**base, **campos}).status_code == codigo


def test_reserva_cancha_inexistente_404_y_sin_token_401(client, ana, reloj, reservar):
    assert reservar(ana, str(uuid.uuid4())).status_code == 404
    assert client.post("/api/reservas", json={}).status_code == 401


def test_partido_abierto_exige_3_horas_de_anticipacion(client, ana, cancha, reloj, reservar):
    reloj.fijar(a_utc(iso(0, 12)))                                  # lunes 12:00 local
    assert reservar(ana, cancha, dia=0, hora=14, es_partido_abierto=True).status_code == 422   # en 2 h
    assert reservar(ana, cancha, dia=0, hora=16, es_partido_abierto=True).status_code == 201   # en 4 h


# ---------------------------------------------------------------- regla 4
def test_regla4_mismo_horario_409_con_mensaje_claro(ana, beto, cancha, reloj, reservar):
    assert reservar(ana, cancha).status_code == 201
    r = reservar(beto, cancha)
    assert r.status_code == 409 and "lista de espera" in r.json()["detail"]


@pytest.mark.parametrize("hora,horas", [(9, 2), (10, 1), (10, 3), (11, 2)])
def test_regla4_traslape_parcial_409(ana, beto, cancha, reloj, reservar, hora, horas):
    assert reservar(ana, cancha, hora=10, horas=2).status_code == 201     # ocupa 10:00-12:00
    assert reservar(beto, cancha, hora=hora, horas=horas).status_code == 409


def test_regla4_turnos_contiguos_y_otras_canchas_si_se_permiten(client, db, ana, beto, cancha, reloj, reservar):
    from app.models import Cancha
    otra = Cancha(nombre="Pádel 2", deporte="padel", tarifa_hora=400, deposito=0)
    db.add(otra)
    db.commit()
    assert reservar(ana, cancha, hora=10, horas=2).status_code == 201     # 10-12
    assert reservar(beto, cancha, hora=12, horas=1).status_code == 201    # 12-13: pegado, sin traslape
    assert reservar(beto, str(otra.id), hora=10, horas=2).status_code == 201   # otra cancha, mismo horario


def test_regla4_dos_peticiones_simultaneas_solo_una_gana(ana, beto, cancha, reloj):
    def intentar(usuario):
        with TestClient(app) as c:
            return c.post("/api/reservas", headers=usuario.h,
                          json={"cancha_id": cancha, "inicio": iso(1, 19), "horas": 1}).status_code
    with ThreadPoolExecutor(2) as pool:
        codigos = sorted(pool.map(intentar, [ana, beto]))
    assert codigos == [201, 409]


# ---------------------------------------------------------------- material
@pytest.fixture
def raquetas(db):
    m = Material(nombre="Raqueta", tipo="raqueta", cantidad_total=3, precio_renta=50)
    db.add(m)
    db.commit()
    return str(m.id)


def test_material_dentro_del_inventario_y_excedido(client, db, ana, beto, cancha, raquetas, reloj, reservar):
    r = reservar(ana, cancha, hora=10, materiales=[{"material_id": raquetas, "cantidad": 2}])
    assert r.status_code == 201
    # mismo horario en OTRA cancha: solo queda 1 raqueta
    from app.models import Cancha
    otra = Cancha(nombre="Pádel 2", deporte="padel", tarifa_hora=400, deposito=0)
    db.add(otra)
    db.commit()
    exceso = reservar(beto, str(otra.id), hora=10, materiales=[{"material_id": raquetas, "cantidad": 2}])
    assert exceso.status_code == 409 and "disponibles: 1" in exceso.json()["detail"]
    assert reservar(beto, str(otra.id), hora=10, materiales=[{"material_id": raquetas, "cantidad": 1}]).status_code == 201
    # a otra hora el inventario vuelve a estar completo
    assert reservar(beto, str(otra.id), hora=15, materiales=[{"material_id": raquetas, "cantidad": 3}]).status_code == 201


def test_material_fallido_no_deja_reserva_a_medias(client, db, ana, cancha, raquetas, reloj, reservar):
    assert reservar(ana, cancha, materiales=[{"material_id": raquetas, "cantidad": 9}]).status_code == 409
    assert db.scalar(select(func.count()).select_from(Reserva)) == 0     # ni reserva ni evento huérfanos
    assert db.scalar(select(func.count()).select_from(OutboxEvento)) == 0
    assert reservar(ana, cancha).status_code == 201                      # el horario quedó libre


def test_material_repetido_inexistente_o_inactivo(client, db, ana, cancha, raquetas, reloj, reservar):
    dup = [{"material_id": raquetas, "cantidad": 1}] * 2
    assert reservar(ana, cancha, materiales=dup).status_code == 422
    assert reservar(ana, cancha, materiales=[{"material_id": str(uuid.uuid4()), "cantidad": 1}]).status_code == 404
    db.query(Material).update({"activo": False})
    db.commit()
    assert reservar(ana, cancha, materiales=[{"material_id": raquetas, "cantidad": 1}]).status_code == 404


# ---------------------------------------------------------------- pago y expiración
def test_pagar_confirma_registra_pago_y_eventos(client, db, ana, cancha, reloj, reservar):
    rid = reservar(ana, cancha).json()["id"]
    r = client.post(f"/api/reservas/{rid}/pagar", headers=ana.h)
    assert r.status_code == 200 and r.json()["estado"] == "confirmada" and r.json()["expira_en"] is None
    pago = db.scalars(select(Pago)).one()
    assert pago.tipo == "deposito" and pago.estado == "aprobado" and pago.monto == 100
    tipos = sorted(e.tipo for e in db.scalars(select(OutboxEvento)))
    assert tipos == ["cambio_estado", "cambio_estado", "pago"]
    assert client.post(f"/api/reservas/{rid}/pagar", headers=ana.h).status_code == 409   # no se paga dos veces
    assert db.scalar(select(func.count()).select_from(Pago)) == 1


def test_solo_el_dueno_paga(client, ana, beto, admin, cancha, reloj, reservar):
    rid = reservar(ana, cancha).json()["id"]
    assert client.post(f"/api/reservas/{rid}/pagar", headers=beto.h).status_code == 404    # ni siquiera sabe que existe
    assert client.post(f"/api/reservas/{rid}/pagar", headers=admin.h).status_code == 403
    assert client.post(f"/api/reservas/{uuid.uuid4()}/pagar", headers=ana.h).status_code == 404


def test_deposito_cero_confirma_sin_pago(client, db, ana, reloj, reservar):
    from app.models import Cancha
    gratis = Cancha(nombre="Libre", deporte="tenis", tarifa_hora=100, deposito=0)
    db.add(gratis)
    db.commit()
    rid = reservar(ana, str(gratis.id)).json()["id"]
    assert client.post(f"/api/reservas/{rid}/pagar", headers=ana.h).json()["estado"] == "confirmada"
    assert db.scalar(select(func.count()).select_from(Pago)) == 0


def test_borrador_vencido_no_se_puede_pagar_y_libera_el_horario(client, ana, beto, cancha, reloj, reservar):
    rid = reservar(ana, cancha).json()["id"]
    reloj.avanzar(minutes=11)
    r = client.post(f"/api/reservas/{rid}/pagar", headers=ana.h)
    assert r.status_code == 410
    assert client.get(f"/api/reservas/{rid}", headers=ana.h).json()["estado"] == "expirada"
    assert reservar(beto, cancha).status_code == 201                       # el turno volvió a estar libre


def test_tarea_expirar_borradores(db, ana, cancha, reloj, reservar):
    from app.servicios import reservas as srv
    reservar(ana, cancha, hora=10)
    reservar(ana, cancha, hora=12)
    reloj.avanzar(minutes=9)
    assert srv.expirar_borradores(db) == 0
    reloj.avanzar(minutes=2)
    assert srv.expirar_borradores(db) == 2
    assert {r.estado for r in db.scalars(select(Reserva))} == {"expirada"}


# ---------------------------------------------------------------- consulta
def test_cada_quien_ve_lo_suyo_y_el_admin_todo(client, ana, beto, admin, cancha, reloj, reservar):
    rid = reservar(ana, cancha, hora=10).json()["id"]
    reservar(beto, cancha, hora=12)
    assert client.get("/api/reservas", headers=ana.h).json()["total"] == 1
    assert client.get("/api/reservas", headers=admin.h).json()["total"] == 2
    assert client.get(f"/api/reservas/{rid}", headers=beto.h).status_code == 404
    assert client.get(f"/api/reservas/{rid}", headers=admin.h).status_code == 200
    assert client.get("/api/reservas").status_code == 401


def test_filtros_y_paginacion_de_reservas(client, ana, admin, cancha, reloj, reservar, confirmada):
    for h in (10, 12, 14):
        reservar(ana, cancha, hora=h)
    confirmada(ana, cancha, hora=16)
    assert client.get("/api/reservas?estado=confirmada", headers=ana.h).json()["total"] == 1
    assert client.get("/api/reservas?estado=borrador", headers=ana.h).json()["total"] == 3
    pag = client.get("/api/reservas?tamano=3&pagina=2", headers=ana.h).json()
    assert pag["total"] == 4 and len(pag["items"]) == 1
    assert client.get("/api/reservas?estado=inventado", headers=ana.h).status_code == 422
    assert client.get(f"/api/reservas?cancha_id={uuid.uuid4()}", headers=admin.h).json()["total"] == 0
    # orden: la más próxima primero hacia atrás en el tiempo
    horas = [a_utc(i["inicio"]) for i in client.get("/api/reservas", headers=ana.h).json()["items"]]
    assert horas == sorted(horas, reverse=True)
