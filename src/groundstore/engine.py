"""Engine and schema helpers, including oa-configurator resolution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from oa_configurator import ResolvedDatabase, Resolver
from sqlalchemy import Engine, event
from sqlalchemy.engine import URL

from .models import Base


def create_groundstore_engine(
    url: str | URL | None = None,
    *,
    resolved_database: ResolvedDatabase | None = None,
    resolver: Resolver | None = None,
    database_name: str = "mapping_db",
    execution_options: Mapping[str, Any] | None = None,
    **engine_kwargs: Any,
) -> Engine:
    """Create a store engine from an explicit URL or OA database resource."""
    if sum(value is not None for value in (url, resolved_database, resolver)) > 1:
        raise ValueError("provide only one of url, resolved_database, or resolver")

    if resolved_database is not None:
        engine = resolved_database.create_engine(**engine_kwargs)
    elif resolver is not None:
        engine = resolver.resolve_database(database_name).create_engine(**engine_kwargs)
    elif url is not None:
        engine = sa.create_engine(url, **engine_kwargs)
    else:
        engine = (
            Resolver.from_active_config()
            .resolve_database(database_name)
            .create_engine(**engine_kwargs)
        )

    if execution_options:
        engine = engine.execution_options(**dict(execution_options))
    if engine.dialect.name == "sqlite":
        _enable_sqlite_foreign_keys(engine)
    return engine


def create_schema(engine: Engine) -> None:
    """Create all groundstore tables if they do not already exist."""
    Base.metadata.create_all(engine)


def drop_schema(engine: Engine) -> None:
    """Drop all groundstore tables; intended for isolated test databases."""
    Base.metadata.drop_all(engine)


def _enable_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
