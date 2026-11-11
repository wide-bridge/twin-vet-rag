"""PostgreSQL schema (docs/03). Times are UTC (timestamptz)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ------------------------------------------------------------------ consultation side
class Session(Base):
    __tablename__ = "session"
    id: Mapped[uuid.UUID] = _pk()
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class Consultation(Base):
    __tablename__ = "consultation"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id", ondelete="CASCADE"), nullable=False, index=True)
    dog_name: Mapped[str | None] = mapped_column(String(20))
    dog_breed: Mapped[str] = mapped_column(String(40), nullable=False)
    dog_age_months: Mapped[int] = mapped_column(Integer, nullable=False)  # at creation (computed or estimated)
    dog_birth_date: Mapped[date | None] = mapped_column(Date)  # unknown parts stored as 1; see dog_age_basis
    dog_age_basis: Mapped[str] = mapped_column(String(8), nullable=False, server_default="estimate")
    dog_weight_kg: Mapped[float | None] = mapped_column(Numeric(4, 1))
    dog_neutered: Mapped[str | None] = mapped_column(String(8))
    dog_health_notes: Mapped[str | None] = mapped_column(String(300))
    life_stage: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        CheckConstraint("dog_age_months BETWEEN 0 AND 371", name="ck_consultation_age"),
        CheckConstraint("dog_neutered IN ('yes','no','unknown')", name="ck_consultation_neutered"),
        CheckConstraint("dog_age_basis IN ('day','month','year','estimate')", name="ck_consultation_age_basis"),
        CheckConstraint("(dog_age_basis = 'estimate') = (dog_birth_date IS NULL)", name="ck_consultation_birth_date"),
        CheckConstraint("life_stage IN ('puppy','adult','senior')", name="ck_consultation_life_stage"),
        CheckConstraint("status IN ('active')", name="ck_consultation_status"),
    )


class Message(Base):
    __tablename__ = "message"
    id: Mapped[uuid.UUID] = _pk()
    consultation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("consultation.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("consultation_id", "sequence", name="uq_message_sequence"),
        CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),
    )


class AnswerRun(Base):
    __tablename__ = "answer_run"
    id: Mapped[uuid.UUID] = _pk()
    consultation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("consultation.id", ondelete="CASCADE"), nullable=False)
    question_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("message.id", ondelete="CASCADE"), nullable=False)
    answer_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("message.id", ondelete="SET NULL"))
    client_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # same id + different text -> 409
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    response_type: Mapped[str | None] = mapped_column(String(24))
    decision_stage: Mapped[str | None] = mapped_column(String(24))  # scope / clarify / retrieval / answer
    error_code: Mapped[str | None] = mapped_column(String(48))
    model: Mapped[str | None] = mapped_column(String(64))
    classifier_model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    index_version: Mapped[str | None] = mapped_column(String(96))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict | None] = mapped_column(JSONB)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("consultation_id", "client_request_id", name="uq_answer_run_request"),
        CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_answer_run_status"),
        CheckConstraint(
            "response_type IS NULL OR response_type IN ('grounded','clarification','insufficient_evidence','out_of_scope')",
            name="ck_answer_run_response_type",
        ),
        CheckConstraint("(status = 'completed') = (response_type IS NOT NULL)", name="ck_answer_run_completed_has_type"),
        Index("ix_answer_run_status_started", "status", "started_at"),
    )


class Citation(Base):
    __tablename__ = "citation"
    id: Mapped[uuid.UUID] = _pk()
    answer_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_run.id", ondelete="CASCADE"), nullable=False, index=True)
    # chunk may disappear when a source is deleted; the citation then reports "not available"
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chunk.id", ondelete="SET NULL"))
    title_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)  # internal trace only; exposure gated (docs/03 §6)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[uuid.UUID] = _pk()
    answer_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("message.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("session.id", ondelete="CASCADE"), nullable=False)
    helpful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("answer_message_id", "session_id", name="uq_feedback_once"),)


# ------------------------------------------------------------------ knowledge side
class Document(Base):
    __tablename__ = "document"
    id: Mapped[uuid.UUID] = _pk()
    dataset_code: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(128), nullable=False)
    document_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str | None] = mapped_column(String(16))
    disease_names: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    life_stages: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)  # public http(s) only; never a local path
    usage_note: Mapped[str | None] = mapped_column(Text)
    excerpt_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_version.id", ondelete="SET NULL", use_alter=True, name="fk_document_active_version")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    meta: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("dataset_code", "source_record_id", name="uq_document_source"),
        CheckConstraint("document_type IN ('qa_case','reference')", name="ck_document_type"),
        CheckConstraint("status IN ('active','inactive','deleted')", name="ck_document_status"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_version"
    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False)  # relative source path
    prepared_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_run.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="staging")
    created_at: Mapped[datetime] = _now()
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_version"),
        CheckConstraint("status IN ('staging','active','inactive','failed')", name="ck_document_version_status"),
    )


class Chunk(Base):
    __tablename__ = "chunk"
    id: Mapped[uuid.UUID] = _pk()
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[dict | None] = mapped_column(JSONB)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedding_version: Mapped[str | None] = mapped_column(String(96))  # index version, e.g. aihub59/v1+text-embedding-3-small
    __table_args__ = (
        UniqueConstraint("document_version_id", "ordinal", name="uq_chunk_ordinal"),
        Index(
            "ix_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_run"
    id: Mapped[uuid.UUID] = _pk()
    dataset_code: Mapped[str] = mapped_column(String(32), nullable=False)
    prepared_version: Mapped[str] = mapped_column(String(32), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="running")
    stage: Mapped[str | None] = mapped_column(String(16))
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("status IN ('running','completed','failed')", name="ck_ingestion_run_status"),)
