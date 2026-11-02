"""Reloj y calendario. Todo el código de negocio pide la hora aquí (no con datetime.now),
así las pruebas pueden fijar el reloj y comprobar las reglas por horas de anticipación."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def zona() -> ZoneInfo:
    return ZoneInfo(get_settings().zona_horaria)


def es_horario_estelar(inicio: datetime) -> bool:
    """Horario estelar: de lunes a viernes, turnos que empiezan entre 18:00 y 21:59 (hora local)."""
    local = inicio.astimezone(zona())
    return local.weekday() < 5 and 18 <= local.hour < 22
