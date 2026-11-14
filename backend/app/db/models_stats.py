"""Service usage statistics (서비스 이용통계): one row per KST day with counts only — no ids, text or personal data.

flow  = what happened that day (new consultations, questions, answers by type, ...), recomputed while that day's raw
        records are still kept (consultations are deleted after the retention period), then frozen.
stock = levels when the row was last written (kept profiles, live sessions, ...); a past day keeps the last value
        written on that day, so the stock history fills in as the page or the daily job runs.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    day: Mapped[date] = mapped_column(Date, primary_key=True)  # KST
    flow: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    stock: Mapped[dict | None] = mapped_column(JSONB)
    flow_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    stock_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
