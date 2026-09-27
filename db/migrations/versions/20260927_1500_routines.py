"""routines: group workouts as days of a routine

Revision ID: 8f3a2c61d7e5
Revises: 5d1c7e2a9b40
Create Date: 2026-09-27 15:00:00.000000

Every existing workout becomes the single day of a new routine with the same
name, owner and timestamps, so nothing changes for logged sessions and sets.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f3a2c61d7e5'
down_revision: str | Sequence[str] | None = '5d1c7e2a9b40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'routines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('routines', schema=None) as batch_op:
        batch_op.create_index('ix_routines_user_deleted', ['user_id', 'deleted_at'], unique=False)

    with op.batch_alter_table('workouts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('routine_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('position', sa.Integer(), nullable=False, server_default='0'))

    # Backfill: one single-day routine per existing workout.
    conn = op.get_bind()
    workouts = conn.execute(
        sa.text('SELECT id, user_id, name, created_at, updated_at, deleted_at FROM workouts')
    ).all()
    for w in workouts:
        routine_id = conn.execute(
            sa.text(
                'INSERT INTO routines (user_id, name, created_at, updated_at, deleted_at) '
                'VALUES (:user_id, :name, :created_at, :updated_at, :deleted_at)'
            ),
            dict(user_id=w.user_id, name=w.name, created_at=w.created_at,
                 updated_at=w.updated_at, deleted_at=w.deleted_at),
        ).lastrowid
        conn.execute(
            sa.text('UPDATE workouts SET routine_id = :rid WHERE id = :wid'),
            dict(rid=routine_id, wid=w.id),
        )

    with op.batch_alter_table('workouts', schema=None) as batch_op:
        batch_op.alter_column('routine_id', existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column('position', server_default=None)
        batch_op.create_index(batch_op.f('ix_workouts_routine_id'), ['routine_id'], unique=False)
        batch_op.create_foreign_key('fk_workouts_routine_id_routines', 'routines', ['routine_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('workouts', schema=None) as batch_op:
        batch_op.drop_constraint('fk_workouts_routine_id_routines', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_workouts_routine_id'))
        batch_op.drop_column('position')
        batch_op.drop_column('routine_id')
    with op.batch_alter_table('routines', schema=None) as batch_op:
        batch_op.drop_index('ix_routines_user_deleted')
    op.drop_table('routines')
