"""Staff inbox for messages the agent hands over

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("attention", sa.String(length=16), nullable=True))
    op.add_column("messages", sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("messages", sa.Column("handled_by_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_messages_attention"), "messages", ["attention"], unique=False)
    op.create_foreign_key(
        op.f("fk_messages_handled_by_id_users"), "messages", "users", ["handled_by_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_messages_handled_by_id_users"), "messages", type_="foreignkey")
    op.drop_index(op.f("ix_messages_attention"), table_name="messages")
    op.drop_column("messages", "handled_by_id")
    op.drop_column("messages", "handled_at")
    op.drop_column("messages", "attention")
