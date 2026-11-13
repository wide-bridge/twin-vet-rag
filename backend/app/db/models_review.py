"""MVP1 HITL tables (docs/03 §4): reviewers, ontology 0.1, review bundles/labels/events, knowledge candidates.

Review records and knowledge candidates are never searched by RAG; only activated documents are.
Imported by backend.app.db.models so Alembic and the app see one metadata.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _now, _pk


class Reviewer(Base):
    """Vet or operator using the review screens. The raw access key is never stored."""
    __tablename__ = "reviewer"
    id: Mapped[uuid.UUID] = _pk()
    role: Mapped[str] = mapped_column(String(8), nullable=False)
    display_name: Mapped[str] = mapped_column(String(40), nullable=False)
    email: Mapped[str | None] = mapped_column(String(120))
    credential_status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="unverified")
    access_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = _now()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("access_key_hash", name="uq_reviewer_access_key"),
        CheckConstraint("role IN ('vet','operator')", name="ck_reviewer_role"),
        CheckConstraint("credential_status IN ('unverified','verified')", name="ck_reviewer_credential"),
    )


class ReviewerSession(Base):
    __tablename__ = "reviewer_session"
    id: Mapped[uuid.UUID] = _pk()
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reviewer.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class Concept(Base):
    """Ontology 0.1 concept (M01). Draft until a vet confirms it."""
    __tablename__ = "concept"
    id: Mapped[uuid.UUID] = _pk()
    scheme: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    synonyms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    definition: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    source_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("scheme", "code", name="uq_concept_code"),
        CheckConstraint("scheme IN ('topic','body_part','symptom','condition','test','observation','action')",
                        name="ck_concept_scheme"),
        CheckConstraint("status IN ('draft','approved','retired')", name="ck_concept_status"),
    )


class ConceptRelation(Base):
    __tablename__ = "concept_relation"
    id: Mapped[uuid.UUID] = _pk()
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id", ondelete="CASCADE"), nullable=False, index=True)
    predicate: Mapped[str] = mapped_column(String(20), nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id", ondelete="CASCADE"), nullable=False, index=True)
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    source_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        CheckConstraint("predicate IN ('is_a','part_of','located_in','associated_with','red_flag_for','recommends')",
                        name="ck_concept_relation_predicate"),
        CheckConstraint("status IN ('draft','approved','retired')", name="ck_concept_relation_status"),
        CheckConstraint("subject_id <> object_id", name="ck_concept_relation_not_self"),
    )


class ReviewItem(Base):
    """Review bundle: a frozen copy of question, context, dog, AI answer, citations and versions."""
    __tablename__ = "review_item"
    id: Mapped[uuid.UUID] = _pk()
    origin: Mapped[str] = mapped_column(String(12), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(160), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    history: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    dog: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    ai_answer: Mapped[str] = mapped_column(Text, nullable=False)
    response_type: Mapped[str | None] = mapped_column(String(24))
    citations: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    versions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    show_ai_suggestion: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="draft")
    dispute_type: Mapped[str | None] = mapped_column(String(24))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("origin", "source_ref", name="uq_review_item_source"),
        CheckConstraint("origin IN ('dataset','mock','consultation')", name="ck_review_item_origin"),
        CheckConstraint("(origin = 'mock') = is_mock", name="ck_review_item_mock"),
        CheckConstraint("review_status IN ('draft','vet_reviewed','independently_verified','disputed')",
                        name="ck_review_item_status"),
        CheckConstraint("dispute_type IS NULL OR dispute_type IN ('info_lacking','evidence_conflict','acceptable_difference')",
                        name="ck_review_item_dispute"),
        Index("ix_review_item_status", "review_status"),
    )


class ReviewLabel(Base):
    __tablename__ = "review_label"
    id: Mapped[uuid.UUID] = _pk()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_item.id", ondelete="CASCADE"), nullable=False, index=True)
    author_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reviewer.id", ondelete="RESTRICT"), index=True)
    state: Mapped[str] = mapped_column(String(10), nullable=False, server_default="draft")
    ai_suggestion_shown: Mapped[bool | None] = mapped_column(Boolean)
    accuracy: Mapped[str | None] = mapped_column(String(16))
    evidence_support: Mapped[str | None] = mapped_column(String(12))
    sufficiency: Mapped[str | None] = mapped_column(String(16))
    guidance: Mapped[str | None] = mapped_column(String(20))
    safety_issue: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    revised_answer: Mapped[str | None] = mapped_column(Text)
    revision_reason: Mapped[str | None] = mapped_column(Text)
    revision_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    reuse_scope: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    __table_args__ = (
        CheckConstraint("author_kind IN ('vet','operator','ai')", name="ck_review_label_author"),
        CheckConstraint("(author_kind = 'ai') = (reviewer_id IS NULL)", name="ck_review_label_reviewer"),
        CheckConstraint("state IN ('draft','submitted')", name="ck_review_label_state"),
        CheckConstraint("(state = 'submitted') = (submitted_at IS NOT NULL)", name="ck_review_label_submitted"),
        # one label per reviewer per item; one AI suggestion per item
        Index("uq_review_label_reviewer", "item_id", "reviewer_id", unique=True,
              postgresql_where="reviewer_id IS NOT NULL"),
        Index("uq_review_label_ai", "item_id", unique=True, postgresql_where="author_kind = 'ai'"),
    )


class LabelConcept(Base):
    __tablename__ = "label_concept"
    label_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_label.id", ondelete="CASCADE"), primary_key=True)
    concept_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("concept.id", ondelete="RESTRICT"), primary_key=True)
    assertion: Mapped[str] = mapped_column(String(16), primary_key=True)
    __table_args__ = (
        CheckConstraint("assertion IN ('owner_reported','ai_inferred','vet_assessed','test_confirmed')",
                        name="ck_label_concept_assertion"),
    )


class KnowledgeCandidate(Base):
    """Knowledge to publish. Approval binds to content_hash; editing the content clears it."""
    __tablename__ = "knowledge_candidate"
    id: Mapped[uuid.UUID] = _pk()
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_item.id", ondelete="SET NULL"), index=True)
    label_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_label.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False)
    publish_status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="unapproved")
    approved_hash: Mapped[str | None] = mapped_column(String(64))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reviewer.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eval_result: Mapped[dict | None] = mapped_column(JSONB)
    eval_passed: Mapped[bool | None] = mapped_column(Boolean)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reviewer.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("kind IN ('case','general')", name="ck_knowledge_candidate_kind"),
        CheckConstraint("publish_status IN ('unapproved','approved','active','rejected','retired')",
                        name="ck_knowledge_candidate_status"),
        CheckConstraint("publish_status NOT IN ('approved','active') OR approved_hash IS NOT NULL",
                        name="ck_knowledge_candidate_approved"),
    )


class ReviewEvent(Base):
    """Append-only history of state changes (submit, status change, dispute, approval, activation)."""
    __tablename__ = "review_event"
    id: Mapped[uuid.UUID] = _pk()
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_item.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_candidate.id", ondelete="CASCADE"), index=True)
    actor_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reviewer.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        CheckConstraint("actor_kind IN ('vet','operator','ai','system')", name="ck_review_event_actor"),
        CheckConstraint("item_id IS NOT NULL OR candidate_id IS NOT NULL", name="ck_review_event_target"),
    )
