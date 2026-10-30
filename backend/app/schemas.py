"""Esquemas de entrada y salida de la API (validación en servidor)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Validación simple de correo (sin consultar DNS; permite dominios de laboratorio como .test)
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegistroIn(BaseModel):
    email: str = Field(max_length=255, pattern=EMAIL_PATTERN)
    nombre: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(max_length=255, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # segundos


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    nombre: str
    rol: str
    ranking_puntos: int
    creado_en: datetime
