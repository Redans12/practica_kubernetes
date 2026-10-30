"""Sondas de salud: liveness no depende de las bases; readiness sí."""
from app.config import get_settings


def test_live_responde_aunque_mongo_este_caido(client):
    r = client.get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_ready_503_cuando_mongo_no_responde(client):
    # En las pruebas MONGO_URL apunta a un puerto sin servicio: simula una caída de la dependencia
    r = client.get("/health/ready")
    assert r.status_code == 503
    cuerpo = r.json()
    assert cuerpo["dependencias"]["postgres"] is True
    assert cuerpo["dependencias"]["mongo"] is False


def test_ready_200_cuando_las_dos_bases_responden(client, monkeypatch):
    monkeypatch.setattr("app.routers.health.mongo_ping", lambda: True)
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_endpoint_de_replica_apagado_por_defecto(client):
    assert client.get("/health/replica").status_code == 404


def test_endpoint_de_replica_encendido(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "diag_replica_enabled", True)
    r = client.get("/health/replica")
    assert r.status_code == 200 and r.json()["replica"]


def test_cada_respuesta_trae_identificador_de_correlacion(client):
    r = client.get("/health/live", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"
    assert client.get("/health/live").headers["X-Request-ID"]
