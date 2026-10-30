"""Endpoints de salud para las sondas de Kubernetes.

- /health/live  : el proceso responde. NO consulta bases de datos, para que una caída
                  temporal de una BD no provoque reinicios masivos de Pods (liveness).
- /health/ready : el Pod puede recibir tráfico. Consulta PostgreSQL y MongoDB (readiness).
- /health/replica: solo si DIAG_REPLICA_ENABLED=true; sirve para demostrar el balanceo.
"""
import socket

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.mongo import mongo_ping

router = APIRouter(prefix="/health", tags=["salud"])


def _postgres_ok() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("/live")
def live():
    return {"status": "ok"}


@router.get("/ready")
def ready(response: Response):
    dependencias = {"postgres": _postgres_ok(), "mongo": mongo_ping()}
    ok = all(dependencias.values())
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "degradado", "dependencias": dependencias}


@router.get("/replica")
def replica():
    if not get_settings().diag_replica_enabled:
        raise HTTPException(404, "No encontrado")
    return {"replica": socket.gethostname()}
