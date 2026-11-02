"""DELETE solo donde aplica: lista de espera, jugadores de un partido y material de una reserva

El resto de las tablas se queda sin DELETE para canchas_app (las bajas son lógicas).
La cuenta puede no existir (por ejemplo en las pruebas con un PostgreSQL desechable), por eso se comprueba.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLAS = "lista_espera, reserva_jugador, reserva_material"


def upgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'canchas_app') THEN
            GRANT DELETE ON {TABLAS} TO canchas_app;
          END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'canchas_app') THEN
            REVOKE DELETE ON {TABLAS} FROM canchas_app;
          END IF;
        END $$;
    """)
