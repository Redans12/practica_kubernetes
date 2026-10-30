"""Punto de entrada de la API de gestión de canchas."""
import logging
import socket
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import auth, health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("canchas")
REPLICA = socket.gethostname()

settings = get_settings()
app = FastAPI(
    title="Gestión de canchas deportivas", version="0.1.0",
    docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
)

origenes = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if origenes:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origenes,  # orígenes específicos, nunca "*"
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )


@app.middleware("http")
async def registrar_peticion(request: Request, call_next):
    """Un renglón de log por petición, con fecha, réplica e identificador de correlación."""
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    inicio = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("rid=%s replica=%s %s %s -> error no controlado", rid, REPLICA, request.method, request.url.path)
        raise
    ms = (time.perf_counter() - inicio) * 1000
    response.headers["X-Request-ID"] = rid
    log.info("rid=%s replica=%s %s %s -> %s %.1fms", rid, REPLICA, request.method, request.url.path, response.status_code, ms)
    return response


# /health/* lo usan las sondas de Kubernetes (no se publica por el Ingress).
# Todo lo demás vive bajo /api, que es lo único que expone el Ingress.
app.include_router(health.router)
app.include_router(auth.router, prefix="/api")
