"""settings (dog profile + 주치동물병원), clinic master, care summary fields

Same DDL as the SQL applied by hand to Neon on 2026-11-12 (constraint/index names match).

Revision ID: c3e8a1f5b2d4
Revises: 9d4e2b7a1c35
Create Date: 2026-11-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision: str = "c3e8a1f5b2d4"
down_revision: Union[str, Sequence[str], None] = "9d4e2b7a1c35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "clinic",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("public_id", sa.String(40)),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_key", sa.String(100), nullable=False),
        sa.Column("road_address", sa.Text()),
        sa.Column("road_key", sa.String(200)),
        sa.Column("lot_address", sa.Text()),
        sa.Column("public_phone", sa.String(20)),
        sa.Column("crd_x", sa.Float()),
        sa.Column("crd_y", sa.Float()),
        sa.Column("business_status", sa.String(8)),
        sa.Column("public_updated_at", TS),
        sa.Column("kakao_place_id", sa.String(32)),
        sa.Column("kakao_place_url", sa.Text()),
        sa.Column("is_partner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("public_id", name="uq_clinic_public_id"),
        sa.CheckConstraint("source IN ('public', 'manual')", name="ck_clinic_source"),
        sa.CheckConstraint("(source = 'public') = (public_id IS NOT NULL)", name="ck_clinic_public_id"),
        sa.CheckConstraint("business_status IS NULL OR business_status IN ('open', 'paused', 'closed', 'other')",
                           name="ck_clinic_status"),
    )
    op.create_index("ix_clinic_name_key", "clinic", ["name_key"])
    op.create_index("ix_clinic_road_key", "clinic", ["road_key"])
    op.create_index("ix_clinic_kakao_place_id", "clinic", ["kakao_place_id"])

    op.create_table(
        "dog_profile",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(20)),
        sa.Column("breed", sa.String(40), nullable=False),
        sa.Column("age_months", sa.Integer(), nullable=False),
        sa.Column("birth_date", sa.Date()),
        sa.Column("age_basis", sa.String(8), nullable=False),
        sa.Column("weight_kg", sa.Numeric(4, 1)),
        sa.Column("neutered", sa.String(8)),
        sa.Column("health_notes", sa.String(300)),
        sa.Column("clinic_id", UUID(as_uuid=True)),
        sa.Column("clinic_phone", sa.String(20)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TS),
        sa.UniqueConstraint("token_hash", name="uq_dog_profile_token"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinic.id"], name="dog_profile_clinic_id_fkey", ondelete="SET NULL"),
        sa.CheckConstraint("age_months BETWEEN 0 AND 371", name="ck_dog_profile_age"),
        sa.CheckConstraint("neutered IN ('yes', 'no', 'unknown')", name="ck_dog_profile_neutered"),
        sa.CheckConstraint("age_basis IN ('day', 'month', 'year', 'estimate')", name="ck_dog_profile_age_basis"),
        sa.CheckConstraint("(age_basis = 'estimate') = (birth_date IS NULL)", name="ck_dog_profile_birth_date"),
    )
    op.create_index("ix_dog_profile_expires_at", "dog_profile", ["expires_at"])

    op.add_column("consultation", sa.Column("profile_id", UUID(as_uuid=True)))
    op.add_column("consultation", sa.Column("clinic_id", UUID(as_uuid=True)))
    op.add_column("consultation", sa.Column("topic", sa.String(16)))
    op.add_column("consultation", sa.Column("topics", ARRAY(sa.Text())))
    op.add_column("consultation", sa.Column("summary", sa.Text()))
    op.add_column("consultation", sa.Column("vet_advised", sa.Boolean()))
    op.add_column("consultation", sa.Column("summary_turns", sa.Integer()))
    op.add_column("consultation", sa.Column("summary_at", TS))
    op.create_foreign_key("consultation_profile_id_fkey", "consultation", "dog_profile", ["profile_id"], ["id"],
                          ondelete="SET NULL")
    op.create_foreign_key("consultation_clinic_id_fkey", "consultation", "clinic", ["clinic_id"], ["id"],
                          ondelete="SET NULL")
    op.create_index("ix_consultation_profile_id", "consultation", ["profile_id"])


def downgrade() -> None:
    op.drop_index("ix_consultation_profile_id", table_name="consultation")
    op.drop_constraint("consultation_clinic_id_fkey", "consultation", type_="foreignkey")
    op.drop_constraint("consultation_profile_id_fkey", "consultation", type_="foreignkey")
    for col in ("summary_at", "summary_turns", "vet_advised", "summary", "topics", "topic", "clinic_id", "profile_id"):
        op.drop_column("consultation", col)
    op.drop_table("dog_profile")
    op.drop_table("clinic")
