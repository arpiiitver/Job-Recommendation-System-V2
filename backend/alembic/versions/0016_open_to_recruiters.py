"""Seeker opt-in: discoverable by employers' AI shortlist.

Revision ID: 0016_open_to_recruiters
Revises: 0015_copilot
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_open_to_recruiters"
down_revision: str | None = "0015_copilot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("open_to_recruiters", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(
        "ix_profiles_open_to_recruiters",
        "profiles",
        ["open_to_recruiters"],
        postgresql_where=sa.text("open_to_recruiters"),
    )


def downgrade() -> None:
    op.drop_index("ix_profiles_open_to_recruiters", table_name="profiles")
    op.drop_column("profiles", "open_to_recruiters")
