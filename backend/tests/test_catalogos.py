"""Canchas y materiales: permisos, validación, duplicados, búsqueda, paginación, baja lógica y ficha en MongoDB."""
import uuid

import pytest

CANCHA = {"nombre": "Pádel 9", "deporte": "padel", "tarifa_hora": "400.00", "deposito": "100.00"}


def test_admin_crea_cancha_201(client, admin):
    r = client.post("/api/canchas", json=CANCHA, headers=admin.h)
    assert r.status_code == 201
    assert r.json()["nombre"] == "Pádel 9" and r.json()["activa"] is True


def test_jugador_no_puede_crear_cancha_403_y_sin_token_401(client, ana):
    assert client.post("/api/canchas", json=CANCHA, headers=ana.h).status_code == 403
    assert client.post("/api/canchas", json=CANCHA).status_code == 401


def test_cancha_duplicada_409(client, admin):
    assert client.post("/api/canchas", json=CANCHA, headers=admin.h).status_code == 201
    assert client.post("/api/canchas", json=CANCHA, headers=admin.h).status_code == 409


@pytest.mark.parametrize("cambio", [
    {"tarifa_hora": "0"}, {"tarifa_hora": "-5"}, {"tarifa_hora": "abc"}, {"tarifa_hora": "10.123"},
    {"deposito": "-1"}, {"deporte": "golf"}, {"nombre": "x"}, {"nombre": " "}, {"campo_extra": 1},
])
def test_cancha_datos_invalidos_422(client, admin, cambio):
    assert client.post("/api/canchas", json={**CANCHA, **cambio}, headers=admin.h).status_code == 422


def test_listar_busca_filtra_y_pagina(client, admin, ana):
    for i in range(5):
        client.post("/api/canchas", json={**CANCHA, "nombre": f"Pádel {i}"}, headers=admin.h)
    client.post("/api/canchas", json={**CANCHA, "nombre": "Tenis 100%", "deporte": "tenis"}, headers=admin.h)

    pag = client.get("/api/canchas?tamano=2&pagina=3", headers=ana.h).json()
    assert pag["total"] == 6 and len(pag["items"]) == 2 and pag["pagina"] == 3
    assert client.get("/api/canchas?deporte=tenis", headers=ana.h).json()["total"] == 1
    assert client.get("/api/canchas?q=pádel", headers=ana.h).json()["total"] == 5     # sin distinguir mayúsculas
    assert client.get("/api/canchas?q=100%25", headers=ana.h).json()["total"] == 1    # % se busca literal
    assert client.get("/api/canchas?q=%25", headers=ana.h).json()["total"] == 1       # no actúa como comodín
    assert client.get("/api/canchas?tamano=1000", headers=ana.h).status_code == 422
    assert client.get("/api/canchas?pagina=0", headers=ana.h).status_code == 422
    assert client.get("/api/canchas?deporte=golf", headers=ana.h).status_code == 422


def test_actualizar_cancha(client, admin, ana, cancha):
    assert client.patch(f"/api/canchas/{cancha}", json={"tarifa_hora": "450.00"}, headers=ana.h).status_code == 403
    r = client.patch(f"/api/canchas/{cancha}", json={"tarifa_hora": "450.00"}, headers=admin.h)
    assert r.status_code == 200 and r.json()["tarifa_hora"] == "450.00"
    assert client.patch(f"/api/canchas/{cancha}", json={"tarifa_hora": None}, headers=admin.h).status_code == 422
    assert client.patch(f"/api/canchas/{cancha}", json={"tarifa_hora": "0"}, headers=admin.h).status_code == 422
    assert client.patch(f"/api/canchas/{uuid.uuid4()}", json={"deposito": "1"}, headers=admin.h).status_code == 404
    client.post("/api/canchas", json=CANCHA, headers=admin.h)
    assert client.patch(f"/api/canchas/{cancha}", json={"nombre": "Pádel 9"}, headers=admin.h).status_code == 409


def test_id_mal_formado_422_e_inexistente_404(client, ana):
    assert client.get("/api/canchas/no-es-uuid", headers=ana.h).status_code == 422
    assert client.get(f"/api/canchas/{uuid.uuid4()}", headers=ana.h).status_code == 404


def test_baja_logica_de_cancha(client, admin, ana, cancha):
    assert client.delete(f"/api/canchas/{cancha}", headers=ana.h).status_code == 403
    assert client.delete(f"/api/canchas/{cancha}", headers=admin.h).status_code == 204
    assert client.get("/api/canchas", headers=ana.h).json()["total"] == 0
    assert client.get("/api/canchas?incluir_inactivas=true", headers=ana.h).json()["total"] == 1
    # una cancha dada de baja no se puede reservar
    r = client.post("/api/reservas", headers=ana.h, json={"cancha_id": cancha, "inicio": "2026-11-03T10:00:00-06:00", "horas": 1})
    assert r.status_code == 404


def test_no_se_da_de_baja_una_cancha_con_reservas_vigentes(client, admin, ana, cancha, reloj, reservar):
    assert reservar(ana, cancha).status_code == 201
    r = client.delete(f"/api/canchas/{cancha}", headers=admin.h)
    assert r.status_code == 409 and "vigente" in r.json()["detail"]


def test_disponibilidad_lista_turnos_ocupados(client, ana, beto, cancha, reloj, reservar):
    reservar(ana, cancha, dia=1, hora=10)
    reservar(beto, cancha, dia=1, hora=12, horas=2)
    r = client.get(f"/api/canchas/{cancha}/disponibilidad?fecha=2026-11-03", headers=ana.h).json()
    assert len(r["ocupados"]) == 2 and r["ocupados"][0]["estado"] == "borrador"
    assert client.get(f"/api/canchas/{cancha}/disponibilidad?fecha=2026-11-04", headers=ana.h).json()["ocupados"] == []
    assert client.get(f"/api/canchas/{cancha}/disponibilidad?fecha=ayer", headers=ana.h).status_code == 422


# ---------------------------------------------------------------- materiales
MAT = {"nombre": "Raqueta", "tipo": "raqueta", "cantidad_total": 5, "precio_renta": "50.00"}


def test_material_crud_y_permisos(client, admin, ana):
    assert client.post("/api/materiales", json=MAT, headers=ana.h).status_code == 403
    r = client.post("/api/materiales", json=MAT, headers=admin.h)
    assert r.status_code == 201
    mid = r.json()["id"]
    assert client.post("/api/materiales", json=MAT, headers=admin.h).status_code == 409
    assert client.get(f"/api/materiales/{mid}", headers=ana.h).status_code == 200
    assert client.patch(f"/api/materiales/{mid}", json={"cantidad_total": 8}, headers=admin.h).json()["cantidad_total"] == 8
    assert client.get("/api/materiales?q=raq", headers=ana.h).json()["total"] == 1
    assert client.delete(f"/api/materiales/{mid}", headers=admin.h).status_code == 204
    assert client.get("/api/materiales", headers=ana.h).json()["total"] == 0


@pytest.mark.parametrize("cambio", [{"cantidad_total": -1}, {"cantidad_total": 1.5}, {"precio_renta": "-1"},
                                    {"tipo": ""}, {"nombre": ""}, {"extra": True}])
def test_material_datos_invalidos_422(client, admin, cambio):
    assert client.post("/api/materiales", json={**MAT, **cambio}, headers=admin.h).status_code == 422


# ---------------------------------------------------------------- ficha flexible en MongoDB
FICHA = {"dimensiones": {"largo_m": 20, "ancho_m": 10}, "piso": "césped sintético", "techada": False,
         "extras": {"paredes": "cristal"}}


def test_ficha_se_guarda_y_se_lee(client, admin, cancha, mdb):
    assert client.put(f"/api/canchas/{cancha}/ficha", json=FICHA, headers=admin.h).status_code == 200
    r = client.get(f"/api/canchas/{cancha}/ficha", headers=admin.h).json()
    assert r["piso"] == "césped sintético" and r["extras"]["paredes"] == "cristal"
    client.put(f"/api/canchas/{cancha}/ficha", json={"piso": "arcilla"}, headers=admin.h)   # reemplaza, no duplica
    assert mdb["ficha_cancha"].count_documents({}) == 1


def test_ficha_sin_datos_devuelve_vacia_y_exige_admin(client, admin, ana, cancha, mdb):
    assert client.get(f"/api/canchas/{cancha}/ficha", headers=admin.h).json() == {"cancha_id": cancha}
    assert client.get(f"/api/canchas/{cancha}/ficha", headers=ana.h).status_code == 403
    assert client.put(f"/api/canchas/{cancha}/ficha", json=FICHA, headers=ana.h).status_code == 403


def test_ficha_invalida_422(client, admin, cancha, mdb):
    assert client.put(f"/api/canchas/{cancha}/ficha", json={"techada": "tal vez"}, headers=admin.h).status_code == 422
    assert client.put(f"/api/canchas/{cancha}/ficha", json={"desconocido": 1}, headers=admin.h).status_code == 422


def test_ficha_con_mongo_caido_503_y_el_resto_de_la_api_sigue(client, admin, ana, cancha, mdb):
    mdb.caido = True
    assert client.put(f"/api/canchas/{cancha}/ficha", json=FICHA, headers=admin.h).status_code == 503
    assert client.get(f"/api/canchas/{cancha}/ficha", headers=admin.h).status_code == 503
    assert client.get("/api/canchas", headers=ana.h).status_code == 200      # PostgreSQL sigue respondiendo
