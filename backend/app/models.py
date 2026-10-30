"""Modelos de PostgreSQL (ver docs/D04-modelo-de-datos.md).

La restricción de no traslape (regla 4) y las extensiones se crean en la migración
alembic/versions/0001_esquema_inicial.py.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, Range
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("jugador", "administrador")
DEPORTES = ("padel", "futbol7", "tenis")
ESTADOS_RESERVA = (
    "borrador", "confirmada", "en_juego", "completada",
    "liberada_espera", "reasignada", "expirada",
)
# Estados que ocupan el horario de la cancha (regla 4)
ESTADOS_OCUPAN_TURNO = ("borrador", "confirmada", "en_juego")


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )


def _creado_en() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Usuario(Base):
    __tablename__ = "usuario"
    __table_args__ = (
        CheckConstraint("rol IN ('jugador','administrador')", name="usuario_rol_valido"),
        CheckConstraint("ranking_puntos >= 0", name="usuario_ranking_no_negativo"),
        CheckConstraint("intentos_fallidos >= 0", name="usuario_intentos_no_negativos"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    rol: Mapped[str] = mapped_column(String(20), nullable=False, default="jugador", server_default="jugador")
    ranking_puntos: Mapped[int] = mapped_column(Integer, nullable=False, default=1000, server_default="1000")
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # regla 1
    # Límite de intentos de login guardado en BD: funciona igual con varias réplicas
    intentos_fallidos: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    login_bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    creado_en: Mapped[datetime] = _creado_en()


class Cancha(Base):
    __tablename__ = "cancha"
    __table_args__ = (
        CheckConstraint("deporte IN ('padel','futbol7','tenis')", name="cancha_deporte_valido"),
        CheckConstraint("tarifa_hora > 0", name="cancha_tarifa_positiva"),
        CheckConstraint("deposito >= 0", name="cancha_deposito_no_negativo"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    nombre: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    deporte: Mapped[str] = mapped_column(String(20), nullable=False)
    tarifa_hora = mapped_column(Numeric(10, 2), nullable=False)
    deposito = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    creado_en: Mapped[datetime] = _creado_en()


class Material(Base):
    __tablename__ = "material"
    __table_args__ = (
        CheckConstraint("cantidad_total >= 0", name="material_cantidad_no_negativa"),
        CheckConstraint("precio_renta >= 0", name="material_precio_no_negativo"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    nombre: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    cantidad_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    precio_renta = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


class Reserva(Base):
    __tablename__ = "reserva"
    __table_args__ = (
        CheckConstraint(
            "estado IN ('borrador','confirmada','en_juego','completada','liberada_espera','reasignada','expirada')",
            name="reserva_estado_valido",
        ),
        CheckConstraint("deposito >= 0", name="reserva_deposito_no_negativo"),
        CheckConstraint("penalizacion >= 0", name="reserva_penalizacion_no_negativa"),
        CheckConstraint("NOT isempty(periodo)", name="reserva_periodo_no_vacio"),
        # La restricción EXCLUDE (regla 4) se crea en la migración.
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuario.id"), nullable=False)
    cancha_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cancha.id"), nullable=False)
    periodo: Mapped[Range[datetime]] = mapped_column(TSTZRANGE, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="borrador", server_default="borrador")
    es_partido_abierto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    deposito = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    penalizacion = mapped_column(Numeric(10, 2), nullable=False, default=0, server_default="0")
    expira_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = _creado_en()
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ReservaMaterial(Base):
    __tablename__ = "reserva_material"
    __table_args__ = (CheckConstraint("cantidad > 0", name="reserva_material_cantidad_positiva"),)
    reserva_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reserva.id", ondelete="CASCADE"), primary_key=True
    )
    material_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("material.id"), primary_key=True)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)


class Pago(Base):
    """Pagos de una pasarela simulada (no hay dinero real)."""
    __tablename__ = "pago"
    __table_args__ = (
        CheckConstraint("tipo IN ('deposito','penalizacion')", name="pago_tipo_valido"),
        CheckConstraint("estado IN ('pendiente','aprobado','rechazado')", name="pago_estado_valido"),
        CheckConstraint("monto > 0", name="pago_monto_positivo"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    reserva_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reserva.id"), nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    monto = mapped_column(Numeric(10, 2), nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="pendiente", server_default="pendiente")
    creado_en: Mapped[datetime] = _creado_en()


class ReservaJugador(Base):
    """Jugadores de un partido abierto (para el quórum, regla 2)."""
    __tablename__ = "reserva_jugador"
    reserva_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reserva.id", ondelete="CASCADE"), primary_key=True
    )
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuario.id"), primary_key=True)
    confirmado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))


class ListaEspera(Base):
    __tablename__ = "lista_espera"
    id: Mapped[uuid.UUID] = _uuid_pk()
    cancha_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cancha.id"), nullable=False)
    periodo: Mapped[Range[datetime]] = mapped_column(TSTZRANGE, nullable=False)
    usuario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuario.id"), nullable=False)
    creado_en: Mapped[datetime] = _creado_en()


class OutboxEvento(Base):
    """Buzón de salida: eventos pendientes de publicar en MongoDB (ver D04, sección 6)."""
    __tablename__ = "outbox_evento"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tipo: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    creado_en: Mapped[datetime] = _creado_en()
    procesado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
