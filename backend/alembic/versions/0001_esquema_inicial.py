"""Esquema inicial: usuarios, canchas, material, reservas, pagos, espera y outbox

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _creado():
    return sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    # Necesaria para la restricción EXCLUDE con igualdad de uuid + traslape de rangos (regla 4)
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "usuario",
        _id(),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("rol", sa.String(20), nullable=False, server_default="jugador"),
        sa.Column("ranking_puntos", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("bloqueado_hasta", sa.DateTime(timezone=True)),
        sa.Column("intentos_fallidos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("login_bloqueado_hasta", sa.DateTime(timezone=True)),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _creado(),
        sa.UniqueConstraint("email", name="usuario_email_unico"),
        sa.CheckConstraint("rol IN ('jugador','administrador')", name="usuario_rol_valido"),
        sa.CheckConstraint("ranking_puntos >= 0", name="usuario_ranking_no_negativo"),
        sa.CheckConstraint("intentos_fallidos >= 0", name="usuario_intentos_no_negativos"),
    )

    op.create_table(
        "cancha",
        _id(),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("deporte", sa.String(20), nullable=False),
        sa.Column("tarifa_hora", sa.Numeric(10, 2), nullable=False),
        sa.Column("deposito", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _creado(),
        sa.UniqueConstraint("nombre", name="cancha_nombre_unico"),
        sa.CheckConstraint("deporte IN ('padel','futbol7','tenis')", name="cancha_deporte_valido"),
        sa.CheckConstraint("tarifa_hora > 0", name="cancha_tarifa_positiva"),
        sa.CheckConstraint("deposito >= 0", name="cancha_deposito_no_negativo"),
    )

    op.create_table(
        "material",
        _id(),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("cantidad_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("precio_renta", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("nombre", name="material_nombre_unico"),
        sa.CheckConstraint("cantidad_total >= 0", name="material_cantidad_no_negativa"),
        sa.CheckConstraint("precio_renta >= 0", name="material_precio_no_negativo"),
    )

    op.create_table(
        "reserva",
        _id(),
        sa.Column("usuario_id", pg.UUID(as_uuid=True), sa.ForeignKey("usuario.id"), nullable=False),
        sa.Column("cancha_id", pg.UUID(as_uuid=True), sa.ForeignKey("cancha.id"), nullable=False),
        sa.Column("periodo", pg.TSTZRANGE(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, server_default="borrador"),
        sa.Column("es_partido_abierto", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deposito", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("penalizacion", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("expira_en", sa.DateTime(timezone=True)),
        _creado(),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "estado IN ('borrador','confirmada','en_juego','completada','liberada_espera','reasignada','expirada')",
            name="reserva_estado_valido",
        ),
        sa.CheckConstraint("deposito >= 0", name="reserva_deposito_no_negativo"),
        sa.CheckConstraint("penalizacion >= 0", name="reserva_penalizacion_no_negativa"),
        sa.CheckConstraint("NOT isempty(periodo)", name="reserva_periodo_no_vacio"),
    )
    # Regla 4: un turno, una reserva. Dos reservas activas de la misma cancha no pueden traslaparse.
    op.execute(
        """
        ALTER TABLE reserva ADD CONSTRAINT reserva_sin_traslape
        EXCLUDE USING gist (cancha_id WITH =, periodo WITH &&)
        WHERE (estado IN ('borrador','confirmada','en_juego'))
        """
    )
    op.create_index("ix_reserva_usuario", "reserva", ["usuario_id"])
    op.create_index("ix_reserva_cancha", "reserva", ["cancha_id"])
    op.create_index("ix_reserva_estado_periodo", "reserva", ["estado", "periodo"], postgresql_using="gist")

    op.create_table(
        "reserva_material",
        sa.Column("reserva_id", pg.UUID(as_uuid=True), sa.ForeignKey("reserva.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("material_id", pg.UUID(as_uuid=True), sa.ForeignKey("material.id"), primary_key=True),
        sa.Column("cantidad", sa.Integer(), nullable=False),
        sa.CheckConstraint("cantidad > 0", name="reserva_material_cantidad_positiva"),
    )

    op.create_table(
        "pago",
        _id(),
        sa.Column("reserva_id", pg.UUID(as_uuid=True), sa.ForeignKey("reserva.id"), nullable=False),
        sa.Column("tipo", sa.String(20), nullable=False),
        sa.Column("monto", sa.Numeric(10, 2), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, server_default="pendiente"),
        _creado(),
        sa.CheckConstraint("tipo IN ('deposito','penalizacion')", name="pago_tipo_valido"),
        sa.CheckConstraint("estado IN ('pendiente','aprobado','rechazado')", name="pago_estado_valido"),
        sa.CheckConstraint("monto > 0", name="pago_monto_positivo"),
    )
    op.create_index("ix_pago_reserva", "pago", ["reserva_id"])

    op.create_table(
        "reserva_jugador",
        sa.Column("reserva_id", pg.UUID(as_uuid=True), sa.ForeignKey("reserva.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("usuario_id", pg.UUID(as_uuid=True), sa.ForeignKey("usuario.id"), primary_key=True),
        sa.Column("confirmado", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "lista_espera",
        _id(),
        sa.Column("cancha_id", pg.UUID(as_uuid=True), sa.ForeignKey("cancha.id"), nullable=False),
        sa.Column("periodo", pg.TSTZRANGE(), nullable=False),
        sa.Column("usuario_id", pg.UUID(as_uuid=True), sa.ForeignKey("usuario.id"), nullable=False),
        _creado(),
    )
    op.create_index("ix_lista_espera_orden", "lista_espera", ["cancha_id", "creado_en"])

    op.create_table(
        "outbox_evento",
        _id(),
        sa.Column("tipo", sa.String(40), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        _creado(),
        sa.Column("procesado_en", sa.DateTime(timezone=True)),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
    )
    # Índice parcial: solo los eventos pendientes de publicar
    op.create_index(
        "ix_outbox_pendientes", "outbox_evento", ["creado_en"],
        postgresql_where=sa.text("procesado_en IS NULL"),
    )


def downgrade() -> None:
    for tabla in ("outbox_evento", "lista_espera", "reserva_jugador", "pago",
                  "reserva_material", "reserva", "material", "cancha", "usuario"):
        op.drop_table(tabla)
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
