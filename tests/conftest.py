from collections.abc import Iterator

import pytest
from oa_configurator import CDMDatabaseConfig, ConnectionConfig, Resolver, StackConfig
from sqlalchemy.pool import StaticPool

from groundstore import MappingStore, create_groundstore_engine


@pytest.fixture
def omop_engine():
    pytest.importorskip("omop_alchemy")
    from groundstore.cdm import _create_vocabulary_engine

    stack = StackConfig.for_session(
        connections={"local": ConnectionConfig(dialect="sqlite", database_name=":memory:")},
        databases={"cdm": CDMDatabaseConfig(connection="local")},
    )
    engine = _create_vocabulary_engine(Resolver(stack).resolve_database("cdm"))
    yield engine
    engine.dispose()


@pytest.fixture
def store() -> Iterator[MappingStore]:
    engine = create_groundstore_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield MappingStore(engine)
    engine.dispose()
