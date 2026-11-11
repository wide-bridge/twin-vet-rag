"""dog name (optional, shown in the consultation summary and used to address the dog in answers)

Revision ID: 9d4e2b7a1c35
Revises: 7c2a9e1f4b10
Create Date: 2026-11-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9d4e2b7a1c35"
down_revision: Union[str, Sequence[str], None] = "7c2a9e1f4b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("consultation", sa.Column("dog_name", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("consultation", "dog_name")
