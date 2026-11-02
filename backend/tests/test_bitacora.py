"""Outbox -> MongoDB (idempotencia y reintento), historial, reporte, tareas programadas y semilla."""
import json



from sqlalchemy import func, select

from app import semilla, tareas
from app.bitacora import asegurar_colecciones, publicar_pendientes, reconciliar
from app.models import Cancha, OutboxEvento, Pago, Reserva, Usuario
from tests.conftest import iso


def _pendientes(db):
    db.expire_all()
    return db.scalar(select(func.count()).select_from(OutboxEvento).where(OutboxEvento.procesado_en.is_(None)))


def test_publicar_copia_los_eventos_y_marca_procesado(db, ana, cancha, mdb, reloj, confirmada):
    confirmada(ana, cancha)                                        # borrador + pago + confirmada = 3 eventos
    asegurar_colecciones(mdb)
    assert publicar_pendientes(db, mdb) == {"pendientes_leidos": 3, "publicados": 3, "error": None}
    assert _pendientes(db) == 0 and mdb["bitacora_reserva"].count_documents({}) == 3
    doc = mdb["bitacora_reserva"].find_one({"tipo": "pago"})
    assert doc["detalle"]["pasarela"] == "simulada" and doc["evento_id"] and doc["ts"]


def test_publicar_es_idempotente(db, ana, cancha, mdb, reloj, confirmada):
    confirmada(ana, cancha)
    asegurar_colecciones(mdb)
    publicar_pendientes(db, mdb)
    assert publicar_pendientes(db, mdb)["publicados"] == 0         # segunda corrida: nada pendiente
    # Se simula una entrega repetida: la fila vuelve a quedar pendiente pero el evento ya está en Mongo
    db.query(OutboxEvento).update({"procesado_en": None})
    db.commit()
    assert publicar_pendientes(db, mdb)["publicados"] == 3
    assert mdb["bitacora_reserva"].count_documents({}) == 3        # sin duplicados


def test_prueba_E04_mongo_apagado_cinco_cambios_y_luego_aparecen_los_cinco(db, ana, beto, cancha, mdb, reloj, reservar, client):
    asegurar_colecciones(mdb)
    mdb.caido = True
    ids = [reservar(ana if i % 2 else beto, cancha, hora=8 + 2 * i).json()["id"] for i in range(5)]   # 5 cambios de estado
    assert len(ids) == 5 and _pendientes(db) == 5
    r = publicar_pendientes(db, mdb)                               # Mongo sigue apagado
    assert r["publicados"] == 0 and r["error"] == "ServerSelectionTimeoutError"
    assert _pendientes(db) == 5                                    # nada se perdió
    assert db.scalars(select(OutboxEvento.intentos).order_by(OutboxEvento.creado_en)).first() == 1
    mdb.caido = False
    assert publicar_pendientes(db, mdb)["publicados"] == 5
    assert mdb["bitacora_reserva"].count_documents({}) == 5        # ni faltan ni sobran
    assert sorted(d["reserva_id"] for d in mdb["bitacora_reserva"].find()) == sorted(ids)


def test_la_api_de_negocio_sigue_funcionando_con_mongo_apagado(client, ana, cancha, mdb, reloj, reservar):
    mdb.caido = True
    r = reservar(ana, cancha)
    assert r.status_code == 201
    assert client.post(f"/api/reservas/{r.json()['id']}/pagar", headers=ana.h).status_code == 200


def test_historial_lee_la_bitacora_en_orden_y_respeta_permisos(client, db, ana, beto, admin, cancha, mdb, reloj, confirmada):
    rid = confirmada(ana, cancha)
    reloj.avanzar(seconds=5)
    client.post(f"/api/reservas/{rid}/cancelar", headers=ana.h)
    asegurar_colecciones(mdb)
    publicar_pendientes(db, mdb)
    ev = client.get(f"/api/reservas/{rid}/historial", headers=ana.h).json()["eventos"]
    assert [e["a_estado"] for e in ev if e["tipo"] == "cambio_estado"] == ["liberada_espera", "confirmada", "borrador"]
    assert client.get(f"/api/reservas/{rid}/historial", headers=beto.h).status_code == 404
    assert client.get(f"/api/reservas/{rid}/historial", headers=admin.h).status_code == 200
    mdb.caido = True
    assert client.get(f"/api/reservas/{rid}/historial", headers=ana.h).status_code == 503


def test_reconciliar_detecta_eventos_faltantes_en_mongo(db, ana, cancha, mdb, reloj, confirmada):
    confirmada(ana, cancha)
    asegurar_colecciones(mdb)
    publicar_pendientes(db, mdb)
    assert reconciliar(db, mdb) == {"revisados": 3, "faltantes": []}
    borrado = mdb["bitacora_reserva"].find_one()
    mdb["bitacora_reserva"].delete_one({"evento_id": borrado["evento_id"]})
    assert reconciliar(db, mdb)["faltantes"] == [borrado["evento_id"]]


def test_asegurar_colecciones_pide_validacion_e_indices_y_es_repetible(mdb):
    asegurar_colecciones(mdb)
    asegurar_colecciones(mdb)
    assert set(mdb.validadores) == {"bitacora_reserva", "ficha_cancha"}
    assert "$jsonSchema" in mdb.validadores["bitacora_reserva"]
    assert {"uq_evento_id", "ix_reserva_ts", "ix_cancha_ts"} <= set(mdb["bitacora_reserva"].index_information())
    assert mdb["bitacora_reserva"].index_information()["uq_evento_id"]["unique"] is True


# ---------------------------------------------------------------- reporte
def test_reporte_de_ocupacion(client, db, ana, beto, admin, cancha, reloj, confirmada, reservar):
    confirmada(ana, cancha, dia=1, hora=10, horas=2)
    confirmada(beto, cancha, dia=2, hora=10, horas=1)
    reservar(ana, cancha, dia=3, hora=10)                                           # borrador: no cuenta
    q = f"desde={iso(0, 0)}&hasta={iso(7, 0)}".replace("+", "%2B")
    r = client.get(f"/api/reportes/ocupacion?{q}", headers=admin.h).json()
    fila = next(c for c in r["canchas"] if c["cancha_id"] == cancha)
    assert fila["reservas"] == 2 and fila["horas"] == 3.0 and float(fila["ingresos"]) == 200.0
    assert client.get(f"/api/reportes/ocupacion?{q}", headers=ana.h).status_code == 403
    assert client.get(f"/api/reportes/ocupacion?desde={iso(3, 0)}&hasta={iso(1, 0)}", headers=admin.h).status_code == 422
    assert client.get("/api/reportes/ocupacion", headers=admin.h).status_code == 422


# ---------------------------------------------------------------- tareas programadas (CLI)
def test_cli_de_tareas(db, ana, cancha, mdb, reloj, reservar, monkeypatch, capsys):
    monkeypatch.setattr(tareas, "get_mongo_db", lambda: mdb)
    reservar(ana, cancha)
    reloj.avanzar(minutes=11)
    assert tareas.main(["tareas", "expirar"]) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida["tarea"] == "expirar" and salida["expiradas"] == 1 and salida["ts"].startswith("2026-11-02")
    assert tareas.main(["tareas", "publicar"]) == 0
    assert json.loads(capsys.readouterr().out)["publicados"] == 2          # borrador + expirada
    assert tareas.main(["tareas", "reconciliar"]) == 0
    for t in ("quorum", "no-show", "finalizar"):
        assert tareas.main(["tareas", t]) == 0
    capsys.readouterr()
    assert tareas.main(["tareas", "inexistente"]) == 2 and tareas.main(["tareas"]) == 2


def test_cli_reconciliar_con_faltantes_sale_con_codigo_3(db, ana, cancha, mdb, reloj, reservar, monkeypatch, capsys):
    monkeypatch.setattr(tareas, "get_mongo_db", lambda: mdb)
    reservar(ana, cancha)
    tareas.main(["tareas", "publicar"])
    mdb["bitacora_reserva"].delete_many({})
    capsys.readouterr()
    assert tareas.main(["tareas", "reconciliar"]) == 3


def test_cli_publicar_con_mongo_caido_no_falla_y_deja_pendientes(db, ana, cancha, mdb, reloj, reservar, monkeypatch, capsys):
    monkeypatch.setattr(tareas, "get_mongo_db", lambda: mdb)
    reservar(ana, cancha)
    mdb.caido = True
    assert tareas.main(["tareas", "publicar"]) == 0
    assert json.loads(capsys.readouterr().out)["error"] == "ServerSelectionTimeoutError"
    assert _pendientes(db) == 1


# ---------------------------------------------------------------- semilla
def test_semilla_crea_mas_de_100_registros_validos_y_es_idempotente(db, mdb, reloj):
    r = semilla.sembrar(db, "clave-demo-123456", mdb)
    assert r["usuarios"] == 20 and r["canchas"] == 9 and r["materiales"] == 8 and r["reservas"] == 70
    assert r["total_postgres"] > 100 and r["fichas_mongo"] == 9
    assert mdb["ficha_cancha"].count_documents({}) == 9
    admins = db.scalars(select(Usuario).where(Usuario.rol == "administrador")).all()
    assert [a.email for a in admins] == ["admin@ejemplo.test"]
    assert all(u.email.endswith("@ejemplo.test") and u.password_hash.startswith("$argon2") for u in db.scalars(select(Usuario)))
    estados = {e for (e,) in db.execute(select(Reserva.estado).distinct())}
    assert {"completada", "confirmada", "liberada_espera", "expirada"} <= estados
    again = semilla.sembrar(db, "clave-demo-123456", mdb)
    assert "ya estaba aplicada" in again["mensaje"]
    assert db.scalar(select(func.count()).select_from(Reserva)) == 70    # sin duplicar


def test_semilla_permite_iniciar_sesion_y_los_pagos_cuadran(client, db, mdb, reloj):
    semilla.sembrar(db, "clave-demo-123456", mdb)
    assert client.post("/api/auth/login", json={"email": "admin@ejemplo.test", "password": "clave-demo-123456"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "jugador07@ejemplo.test", "password": "clave-demo-123456"}).status_code == 200
    assert db.scalar(select(func.count()).select_from(Pago)) > 0
    assert db.scalar(select(func.count()).select_from(Cancha)) == 9


def test_semilla_exige_contrasena(monkeypatch, capsys):
    monkeypatch.delenv("SEED_PASSWORD", raising=False)
    assert semilla.main() == 2
    monkeypatch.setenv("SEED_PASSWORD", "corta")
    assert semilla.main() == 2
