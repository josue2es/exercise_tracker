"""workout exercise rest seconds

Revision ID: 3b9e4f1a6c27
Revises: 8f3a2c61d7e5
Create Date: 2026-09-28 10:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b9e4f1a6c27'
down_revision: str | Sequence[str] | None = '8f3a2c61d7e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('workout_exercises', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rest_seconds', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('workout_exercises', schema=None) as batch_op:
        batch_op.drop_column('rest_seconds')
