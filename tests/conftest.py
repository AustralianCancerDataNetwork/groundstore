from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool

from groundstore import MappingStore, create_groundstore_engine


@pytest.fixture
def store() -> Iterator[MappingStore]:
    engine = create_groundstore_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield MappingStore(engine)
    engine.dispose()
