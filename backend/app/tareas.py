"""Tareas programadas (CronJobs de Kubernetes). Uso:  python -m app.tareas <tarea>

  expirar     borradores vencidos -> expirada                 (cada minuto)
  quorum      regla 2: partidos abiertos sin quórum a 2 h     (cada 5 minutos)
  no-show     regla 1: sin check-in 15 min después del inicio (cada 5 minutos)
  finalizar   en_juego terminado -> completada + ranking      (cada 5 minutos)
  publicar    outbox -> bitácora en MongoDB                   (cada minuto)
  reconciliar compara PostgreSQL contra MongoDB               (cada hora)

Cada tarea imprime UNA línea JSON (fecha, tarea, resultado): sirve como evidencia en `kubectl logs`.
El código de salida es distinto de 0 si algo falla, para que Kubernetes lo reintente y lo muestre.
"""
import json
import logging
import sys

from app import tiempo
from app.bitacora import asegurar_colecciones, publicar_pendientes, reconciliar
from app.db import get_sessionmaker
from app.mongo import get_mongo_db
from app.servicios import reservas as srv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("canchas.tareas")


def _expirar(db):
    return {"expiradas": srv.expirar_borradores(db)}


def _quorum(db):
    return srv.revisar_quorum(db)


def _no_show(db):
    return {"no_show": srv.marcar_no_show(db)}


def _finalizar(db):
    return {"completadas": srv.finalizar_turnos(db)}


def _publicar(db):
    mdb = get_mongo_db()
    try:
        asegurar_colecciones(mdb)   # idempotente; si Mongo está caído, publicar_pendientes lo reporta
    except Exception as e:  # noqa: BLE001
        log.warning("no se pudo preparar MongoDB: %s", type(e).__name__)
    return publicar_pendientes(db, mdb)


def _reconciliar(db):
    return reconciliar(db, get_mongo_db())


TAREAS = {"expirar": _expirar, "quorum": _quorum, "no-show": _no_show, "finalizar": _finalizar,
          "publicar": _publicar, "reconciliar": _reconciliar}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in TAREAS:
        print(f"Uso: python -m app.tareas {{{'|'.join(TAREAS)}}}", file=sys.stderr)
        return 2
    nombre = argv[1]
    with get_sessionmaker()() as db:
        try:
            resultado = TAREAS[nombre](db)
        except Exception:
            log.exception("la tarea %s falló", nombre)
            return 1
    print(json.dumps({"ts": tiempo.ahora().isoformat(), "tarea": nombre, **resultado}, default=str))
    # reconciliar: faltantes en Mongo es una condición a reportar con código distinto de 0
    return 3 if resultado.get("faltantes") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
