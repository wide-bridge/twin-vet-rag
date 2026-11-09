from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from ..config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().database_url, pool_pre_ping=True, future=True)


def session_factory(url: str | None = None) -> sessionmaker:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)
