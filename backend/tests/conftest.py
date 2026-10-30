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
