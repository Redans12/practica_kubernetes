"""Fixtures de pruebas.

Requieren un PostgreSQL desechable. Ejemplo (Docker):
    docker run -d --name pg-test -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
    set TEST_DATABASE_URL=postgresql+psycopg://postgres:test@127.0.0.1:55432/postgres
Las pruebas BORRAN y recrean el esquema: nunca apuntar a una base con datos reales.
"""
import os
from pathlib import Path

# La configuración se fija ANTES de importar la aplicación
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:55432/canchas_test"
)
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27099")  # puerto sin servicio a propósito
os.environ.setdefault("JWT_SECRET", "secreto-solo-para-pruebas-0123456789abcdef")
os.environ.setdefault("LOGIN_MAX_INTENTOS", "5")

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import get_engine, get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def esquema():
    """Aplica las migraciones reales (así también se prueba la migración y su reversión)."""
    cfg = Config(str(RAIZ / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture(autouse=True)
def limpiar_tablas():
    with get_engine().begin() as conn:
        conn.execute(text(
            "TRUNCATE usuario, cancha, material, reserva, reserva_material, pago, "
            "reserva_jugador, lista_espera, outbox_evento CASCADE"
        ))
    yield


@pytest.fixture
def db():
    sesion = get_sessionmaker()()
    try:
        yield sesion
    finally:
        sesion.rollback()
        sesion.close()


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------- utilidades para pruebas de negocio
import uuid  # noqa: E402
from datetime import date, datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import mongomock  # noqa: E402
from pymongo.errors import ServerSelectionTimeoutError  # noqa: E402
from sqlalchemy import update  # noqa: E402

from app import tiempo  # noqa: E402
from app.models import Cancha, Usuario  # noqa: E402
from app.mongo import get_mdb  # noqa: E402

# Lunes 2 nov 2026, 09:00 hora de Morelia/CDMX (UTC-6, sin horario de verano desde 2022)
RELOJ_INICIAL = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
PASSWORD = "clave-segura-123"


def iso(dias: int, hora: int, minuto: int = 0) -> str:
    """Fecha ISO en hora local (-06:00) a `dias` días del lunes 2-nov-2026."""
    d = date(2026, 11, 2) + timedelta(days=dias)
    return f"{d.isoformat()}T{hora:02d}:{minuto:02d}:00-06:00"


def a_utc(texto_iso: str) -> datetime:
    return datetime.fromisoformat(texto_iso).astimezone(timezone.utc)


@pytest.fixture
def reloj(monkeypatch):
    """Reloj controlable: las reglas por horas de anticipación se prueban sin esperar."""
    estado = {"t": RELOJ_INICIAL}
    monkeypatch.setattr(tiempo, "ahora", lambda: estado["t"])

    def fijar(t):
        estado["t"] = t

    def avanzar(**kw):
        estado["t"] += timedelta(**kw)

    return SimpleNamespace(fijar=fijar, avanzar=avanzar, ahora=lambda: estado["t"])


class MongoConmutable:
    """MongoDB simulado que se puede 'apagar' para probar el reintento del outbox."""

    def __init__(self, base):
        self._base = base
        self.caido = False
        self.validadores = {}

    def _vivo(self):
        if self.caido:
            raise ServerSelectionTimeoutError("MongoDB apagado en la prueba")
        return self._base

    def __getitem__(self, nombre):
        return self._vivo()[nombre]

    def create_collection(self, nombre, **opciones):
        """mongomock no soporta `validator`: se anota que se pidió y se crea sin validación.
        La validación $jsonSchema real solo se puede comprobar contra un MongoDB de verdad (ver E de la guía)."""
        self.validadores[nombre] = opciones.get("validator")
        return self._vivo().create_collection(nombre)

    def __getattr__(self, nombre):
        return getattr(self._vivo(), nombre)


@pytest.fixture
def mdb():
    m = MongoConmutable(mongomock.MongoClient()["canchas_test"])
    app.dependency_overrides[get_mdb] = lambda: m
    yield m
    app.dependency_overrides.pop(get_mdb, None)


@pytest.fixture
def crear_usuario(client, db):
    def _crear(email: str, rol: str = "jugador", nombre: str = "Usuario Prueba"):
        r = client.post("/api/auth/registro", json={"email": email, "nombre": nombre, "password": PASSWORD})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        if rol != "jugador":
            db.execute(update(Usuario).where(Usuario.id == uuid.UUID(uid)).values(rol=rol))
            db.commit()
        token = client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]
        return SimpleNamespace(id=uid, email=email, h={"Authorization": f"Bearer {token}"})
    return _crear


@pytest.fixture
def admin(crear_usuario):
    return crear_usuario("admin@ejemplo.test", "administrador", "Admin")


@pytest.fixture
def ana(crear_usuario):
    return crear_usuario("ana@ejemplo.test", nombre="Ana")


@pytest.fixture
def beto(crear_usuario):
    return crear_usuario("beto@ejemplo.test", nombre="Beto")


@pytest.fixture
def cancha(db):
    c = Cancha(nombre="Pádel 1", deporte="padel", tarifa_hora=400, deposito=100)
    db.add(c)
    db.commit()
    return str(c.id)


@pytest.fixture
def reservar(client):
    """reservar(usuario, cancha, dia, hora, ...) -> respuesta HTTP"""
    def _reservar(usuario, cancha_id, dia=1, hora=10, horas=1, **extra):
        return client.post("/api/reservas", headers=usuario.h, json={
            "cancha_id": cancha_id, "inicio": iso(dia, hora), "horas": horas, **extra})
    return _reservar


@pytest.fixture
def confirmada(client, reservar):
    """confirmada(usuario, cancha, ...) -> id de una reserva ya pagada"""
    def _confirmada(usuario, cancha_id, **kw):
        r = reservar(usuario, cancha_id, **kw)
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        p = client.post(f"/api/reservas/{rid}/pagar", headers=usuario.h)
        assert p.status_code == 200, p.text
        return rid
    return _confirmada
