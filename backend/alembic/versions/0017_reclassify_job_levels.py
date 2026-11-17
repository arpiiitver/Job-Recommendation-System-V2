"""Re-derive experience_level for ingested jobs with the seniority classifier.

The old mapping reused the resume ladder ("developer"/"analyst" => mid, title
checked before years), so ingested listings were almost all "mid".  Only
non-native rows are touched, and rows without any seniority evidence get the
neutral default (mid); employer-chosen levels are never overwritten.  Downgrade is a no-op
(the old labels carried no information worth restoring).

Revision ID: 0017_reclassify_job_levels
Revises: 0016_open_to_recruiters
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_reclassify_job_levels"
down_revision: str | None = "0016_open_to_recruiters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from app.services.seniority import classify_job

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, title, description, experience_level FROM jobs "
            "WHERE coalesce(source, 'native') <> 'native'"
        )
    ).all()
    changed = 0
    for job_id, title, description, current in rows:
        guess = classify_job(title or "", description or "")
        # legacy labels carry no evidence: use the classifier's level, or its
        # neutral default when the posting states nothing
        if guess.level.name != current:
            bind.execute(
                sa.text("UPDATE jobs SET experience_level = CAST(:lvl AS experience_level) WHERE id = :id"),
                {"lvl": guess.level.name, "id": job_id},
            )
            changed += 1
    print(f"0017: re-classified {changed} of {len(rows)} ingested jobs")


def downgrade() -> None:
    pass
