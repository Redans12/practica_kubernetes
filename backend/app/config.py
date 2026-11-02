"""Configuración de la aplicación.

Todo viene de variables de entorno (en Kubernetes: ConfigMap y Secret).
No hay valores por defecto para credenciales: si faltan, la app no arranca.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Conexiones (vienen de un Secret en Kubernetes)
    database_url: str
    mongo_url: str
    mongo_db: str = "canchas"

    # Autenticación
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    login_max_intentos: int = 5
    login_bloqueo_minutos: int = 15

    # Pool de conexiones por réplica.
    # Regla: réplicas_máx x (pool_size + max_overflow) debe ser menor que max_connections de PostgreSQL.
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # Reglas de negocio
    zona_horaria: str = "America/Mexico_City"   # para el horario estelar (18:00 a 22:00, lunes a viernes)
    borrador_minutos: int = 10                  # un borrador bloquea el turno este tiempo

    # CORS: orígenes permitidos separados por coma (vacío = ninguno)
    cors_origins: str = ""

    # Endpoint temporal para demostrar balanceo entre réplicas (apagado por defecto)
    diag_replica_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
