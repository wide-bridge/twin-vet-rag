"""M03 guardian–vet question/answer link (docs/03 §4-1).

A guardian (anonymous dog profile) connects to an operator-registered vet; once the vet accepts, the guardian sends
chosen questions (a frozen copy) and the vet replies. Everything here belongs to the dog profile and is deleted with
it. Nothing here is searched by RAG or copied into HITL automatically.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now, _pk

LINK_STATUSES = ("requested", "accepted", "declined", "cancelled", "ended")
QUESTION_STATUSES = ("sent", "answered", "cancelled")


class VetAccount(Base):
    """A vet answering guardians (separate from HITL reviewers). The raw access key is never stored."""
    __tablename__ = "vet_account"
    id: Mapped[uuid.UUID] = _pk()
    display_name: Mapped[str] = mapped_column(String(40), nullable=False)
    clinic_name: Mapped[str | None] = mapped_column(String(100))
    intro: Mapped[str | None] = mapped_column(String(300))
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    access_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("access_key_hash", name="uq_vet_account_access_key"),)


class VetSession(Base):
    __tablename__ = "vet_session"
    id: Mapped[uuid.UUID] = _pk()
    vet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vet_account.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("token_hash", name="uq_vet_session_token"),)


class VetLink(Base):
    """Guardian (dog profile) ↔ vet connection. One open (requested/accepted) link per pair."""
    __tablename__ = "vet_link"
    id: Mapped[uuid.UUID] = _pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dog_profile.id", ondelete="CASCADE"), nullable=False, index=True)
    vet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vet_account.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="requested")
    requested_at: Mapped[datetime] = _now()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by: Mapped[str | None] = mapped_column(String(8))
    __table_args__ = (
        CheckConstraint(f"status IN {LINK_STATUSES}", name="ck_vet_link_status"),
        CheckConstraint("ended_by IS NULL OR ended_by IN ('guardian','vet','system')", name="ck_vet_link_ended_by"),
        Index("uq_vet_link_open", "profile_id", "vet_id", unique=True,
              postgresql_where=text("status IN ('requested','accepted')")),
    )


class VetQuestion(Base):
    """One question a guardian chose to send, frozen at send time (snapshot), and its reply state."""
    __tablename__ = "vet_question"
    id: Mapped[uuid.UUID] = _pk()
    link_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vet_link.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dog_profile.id", ondelete="CASCADE"), nullable=False, index=True)
    vet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vet_account.id", ondelete="CASCADE"), nullable=False, index=True)
    source_consultation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("consultation.id", ondelete="SET NULL"))
    source_answer_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("message.id", ondelete="SET NULL"))
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    shared_fields: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    quality_use_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    consent_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="sent")
    cancelled_reason: Mapped[str | None] = mapped_column(String(16))
    reply_draft: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    guardian_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_token_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        CheckConstraint(f"status IN {QUESTION_STATUSES}", name="ck_vet_question_status"),
        CheckConstraint("cancelled_reason IS NULL OR cancelled_reason IN ('guardian','link_ended')",
                        name="ck_vet_question_cancelled_reason"),
        UniqueConstraint("access_token_hash", name="uq_vet_question_token"),
        Index("uq_vet_question_open", "source_answer_message_id", "vet_id", unique=True,
              postgresql_where=text("status IN ('sent','answered')")),
    )


class VetReplyRevision(Base):
    """Sent reply and later corrections, in order. Rows are never edited; deleted with the question."""
    __tablename__ = "vet_reply_revision"
    id: Mapped[uuid.UUID] = _pk()
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vet_question.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (CheckConstraint("kind IN ('original','correction')", name="ck_vet_reply_kind"),)
