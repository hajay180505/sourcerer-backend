"""Add drive_nodes.visibility (tri-state public/private/inherit).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "drive_nodes",
        sa.Column(
            "visibility",
            sa.Enum("public", "private", name="node_visibility", native_enum=False, length=8),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("drive_nodes", "visibility")
