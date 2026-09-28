"""workout exercise rir

Revision ID: 6c2d8a4e9f13
Revises: 3b9e4f1a6c27
Create Date: 2026-09-28 11:00:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c2d8a4e9f13'
down_revision: str | Sequence[str] | None = '3b9e4f1a6c27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('workout_exercises', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rir', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('workout_exercises', schema=None) as batch_op:
        batch_op.drop_column('rir')
