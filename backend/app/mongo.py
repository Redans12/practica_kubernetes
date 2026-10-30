"""Conexión a MongoDB (bitácora y ficha de cancha)."""
from functools import lru_cache

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.config import get_settings


@lru_cache
def get_mongo_client() -> MongoClient:
    return MongoClient(
        get_settings().mongo_url,
        serverSelectionTimeoutMS=2000,  # falla rápido si Mongo no está disponible
        appname="canchas-api",
    )


def get_mongo_db():
    return get_mongo_client()[get_settings().mongo_db]


def mongo_ping() -> bool:
    try:
        get_mongo_client().admin.command("ping")
        return True
    except PyMongoError:
        return False
