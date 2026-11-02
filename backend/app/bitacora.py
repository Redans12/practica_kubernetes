"""Bitácora en MongoDB mediante el patrón outbox (ver docs D04, sección 6).

1. El cambio de negocio y una fila de `outbox_evento` se guardan en la MISMA transacción de PostgreSQL.
2. `publicar_pendientes` copia las filas pendientes a Mongo con `evento_id` único (idempotente).
3. Si Mongo no responde, la fila sigue pendiente y se reintenta después. No se pierde ni se duplica nada.
"""
import logging
from datetime import datetime, timedelta

from pymongo.errors import DuplicateKeyError, PyMongoError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.models import OutboxEvento, Reserva

log = logging.getLogger("canchas.bitacora")

TIPOS_EVENTO = ("cambio_estado", "luz_token", "penalizacion", "pago")

VALIDADOR_BITACORA = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["evento_id", "reserva_id", "tipo", "ts"],
    "properties": {
        "evento_id": {"bsonType": "string"},
        "reserva_id": {"bsonType": "string"},
        "cancha_id": {"bsonType": "string"},
        "usuario_id": {"bsonType": "string"},
        "tipo": {"enum": list(TIPOS_EVENTO)},
        "de_estado": {"bsonType": ["string", "null"]},
        "a_estado": {"bsonType": ["string", "null"]},
        "detalle": {"bsonType": "object"},
        "ts": {"bsonType": "date"},
    },
}}
VALIDADOR_FICHA = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["cancha_id"],
    "properties": {
        "cancha_id": {"bsonType": "string"},
        "dimensiones": {"bsonType": "object"},
        "piso": {"bsonType": "string"},
        "techada": {"bsonType": "bool"},
        "iluminacion": {"bsonType": "object"},
        "mantenimientos": {"bsonType": "array"},
        "extras": {"bsonType": "object"},
    },
}}


def emitir(db: Session, tipo: str, reserva: Reserva, *, usuario_id=None,
           de_estado: str | None = None, a_estado: str | None = None, detalle: dict | None = None) -> None:
    """Agrega un evento al outbox dentro de la transacción en curso (no hace commit)."""
    assert tipo in TIPOS_EVENTO, tipo
    db.add(OutboxEvento(tipo=tipo, payload={
        "reserva_id": str(reserva.id),
        "cancha_id": str(reserva.cancha_id),
        "usuario_id": str(usuario_id or reserva.usuario_id),
        "de_estado": de_estado,
        "a_estado": a_estado,
        "detalle": detalle or {},
        "ts": tiempo.ahora().isoformat(),
    }))


def asegurar_colecciones(mdb) -> None:
    """Crea colecciones con validación $jsonSchema e índices. Es idempotente."""
    existentes = set(mdb.list_collection_names())
    if "bitacora_reserva" not in existentes:
        mdb.create_collection("bitacora_reserva", validator=VALIDADOR_BITACORA)
    if "ficha_cancha" not in existentes:
        mdb.create_collection("ficha_cancha", validator=VALIDADOR_FICHA)
    b, f = mdb["bitacora_reserva"], mdb["ficha_cancha"]
    b.create_index("evento_id", unique=True, name="uq_evento_id")
    b.create_index([("reserva_id", 1), ("ts", -1)], name="ix_reserva_ts")
    b.create_index([("cancha_id", 1), ("ts", -1)], name="ix_cancha_ts")
    f.create_index("cancha_id", unique=True, name="uq_cancha_id")
    f.create_index([("mantenimientos.fecha", -1)], name="ix_mantenimientos_fecha")


def publicar_pendientes(db: Session, mdb, limite: int = 200) -> dict:
    """Publica en Mongo los eventos pendientes. Devuelve un resumen para el log del CronJob."""
    filas = db.scalars(
        select(OutboxEvento).where(OutboxEvento.procesado_en.is_(None))
        .order_by(OutboxEvento.creado_en).limit(limite).with_for_update(skip_locked=True)
    ).all()
    publicados = 0
    fallo = None
    for fila in filas:
        p = fila.payload
        doc = {
            "evento_id": str(fila.id), "reserva_id": p["reserva_id"], "cancha_id": p.get("cancha_id"),
            "usuario_id": p.get("usuario_id"), "tipo": fila.tipo,
            "de_estado": p.get("de_estado"), "a_estado": p.get("a_estado"),
            "detalle": p.get("detalle") or {}, "ts": datetime.fromisoformat(p["ts"]),
        }
        try:
            mdb["bitacora_reserva"].update_one({"evento_id": doc["evento_id"]}, {"$setOnInsert": doc}, upsert=True)
        except DuplicateKeyError:
            pass  # ya estaba publicado: el reintento se trata como éxito
        except PyMongoError as e:
            fila.intentos += 1
            fallo = type(e).__name__
            log.warning("outbox: Mongo no disponible (%s); el evento %s sigue pendiente", fallo, fila.id)
            break  # si Mongo está caído no tiene caso insistir con el resto
        fila.procesado_en = tiempo.ahora()
        publicados += 1
    db.commit()
    return {"pendientes_leidos": len(filas), "publicados": publicados, "error": fallo}


def reconciliar(db: Session, mdb, horas: int = 24) -> dict:
    """Compara los eventos ya marcados como procesados en PostgreSQL contra Mongo y reporta faltantes."""
    # creado_en lo pone el reloj de PostgreSQL, así que la ventana también se calcula con el reloj de PostgreSQL
    ids = [str(i) for i in db.scalars(
        select(OutboxEvento.id).where(OutboxEvento.procesado_en.is_not(None),
                                      OutboxEvento.creado_en >= func.now() - timedelta(hours=horas))
    )]
    presentes = {d["evento_id"] for d in mdb["bitacora_reserva"].find({"evento_id": {"$in": ids}}, {"evento_id": 1})}
    faltantes = sorted(set(ids) - presentes)
    return {"revisados": len(ids), "faltantes": faltantes}
