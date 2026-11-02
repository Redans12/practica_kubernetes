"""Esquemas de entrada y salida de la API (validación en servidor)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

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


# ------------------------------------------------------------------ comunes
from decimal import Decimal  # noqa: E402
from typing import Annotated, Generic, Literal, TypeVar  # noqa: E402

T = TypeVar("T")
Deporte = Literal["padel", "futbol7", "tenis"]
Dinero = Annotated[Decimal, Field(max_digits=10, decimal_places=2)]
NombreCorto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]


class Pagina(BaseModel, Generic[T]):
    items: list[T]
    total: int
    pagina: int
    tamano: int


# ------------------------------------------------------------------ canchas
class CanchaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: NombreCorto
    deporte: Deporte
    tarifa_hora: Dinero = Field(gt=0)
    deposito: Dinero = Field(ge=0, default=Decimal("0"))


class CanchaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: NombreCorto | None = None
    deporte: Deporte | None = None
    tarifa_hora: Dinero | None = Field(default=None, gt=0)
    deposito: Dinero | None = Field(default=None, ge=0)


class CanchaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    nombre: str
    deporte: str
    tarifa_hora: Decimal
    deposito: Decimal
    activa: bool


class FichaCancha(BaseModel):
    """Ficha flexible guardada en MongoDB: cada deporte puede traer campos propios en `extras`."""
    model_config = ConfigDict(extra="forbid")
    dimensiones: dict[str, float] | None = None
    piso: str | None = Field(default=None, max_length=60)
    techada: bool | None = None
    iluminacion: dict[str, float] | None = None
    mantenimientos: list[dict[str, str]] | None = Field(default=None, max_length=100)
    extras: dict[str, str | int | float | bool] | None = Field(default=None, max_length=30)


# ------------------------------------------------------------------ materiales
class MaterialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: NombreCorto
    tipo: str = Field(min_length=2, max_length=40)
    cantidad_total: int = Field(ge=0, le=10_000)
    precio_renta: Dinero = Field(ge=0, default=Decimal("0"))


class MaterialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: NombreCorto | None = None
    tipo: str | None = Field(default=None, min_length=2, max_length=40)
    cantidad_total: int | None = Field(default=None, ge=0, le=10_000)
    precio_renta: Dinero | None = Field(default=None, ge=0)


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    nombre: str
    tipo: str
    cantidad_total: int
    precio_renta: Decimal
    activo: bool


# ------------------------------------------------------------------ reservas
def _exigir_zona(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("incluye la zona horaria, por ejemplo 2026-11-05T18:00:00-06:00")
    return v


class MaterialPedido(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material_id: uuid.UUID
    cantidad: int = Field(gt=0, le=100)


class ReservaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancha_id: uuid.UUID
    inicio: datetime
    horas: int = Field(ge=1, le=3)
    es_partido_abierto: bool = False
    materiales: list[MaterialPedido] = Field(default_factory=list, max_length=10)

    _con_zona = field_validator("inicio")(_exigir_zona)


class EsperaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancha_id: uuid.UUID
    inicio: datetime
    horas: int = Field(ge=1, le=3)

    _con_zona = field_validator("inicio")(_exigir_zona)


class JugadorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=255, pattern=EMAIL_PATTERN)


class ReservaOut(BaseModel):
    id: uuid.UUID
    usuario_id: uuid.UUID
    cancha_id: uuid.UUID
    inicio: datetime
    fin: datetime
    estado: str
    es_partido_abierto: bool
    deposito: Decimal
    penalizacion: Decimal
    expira_en: datetime | None

    @classmethod
    def desde(cls, r) -> "ReservaOut":
        return cls(id=r.id, usuario_id=r.usuario_id, cancha_id=r.cancha_id, inicio=r.periodo.lower, fin=r.periodo.upper,
                   estado=r.estado, es_partido_abierto=r.es_partido_abierto, deposito=r.deposito,
                   penalizacion=r.penalizacion, expira_en=r.expira_en)


class CancelacionOut(BaseModel):
    reserva: ReservaOut
    penalizacion: Decimal
    reembolso: Decimal
    reasignada_a_reserva: uuid.UUID | None = None


class EsperaOut(BaseModel):
    id: uuid.UUID
    cancha_id: uuid.UUID
    inicio: datetime
    fin: datetime


class LuzOut(BaseModel):
    token: str
    valido_hasta: datetime
