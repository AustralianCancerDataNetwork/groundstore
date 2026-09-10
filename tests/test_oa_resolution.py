from pathlib import Path

from oa_configurator import (
    ConnectionConfig,
    GenericDatabaseConfig,
    Resolver,
    StackConfig,
)

from groundstore import MappingRunSpec, MappingStore


def test_store_resolves_named_database_resource(tmp_path: Path) -> None:
    stack = StackConfig.for_session(
        connections={
            "mapping_local": ConnectionConfig(
                dialect="sqlite",
                database_name=str(tmp_path / "mapping.db"),
            )
        },
        databases={"mapping_db": GenericDatabaseConfig(connection="mapping_local")},
    )
    resolver = Resolver(stack)
    store = MappingStore.from_database(resolver.resolve_database("mapping_db"))
    run = store.get_or_create_run(
        MappingRunSpec(
            source_namespace="pbs",
            source_fingerprint="snapshot-1",
            target_system="omop",
            algorithm_version="mapper-1",
            policy_version="policy-1",
        )
    )
    assert run.source_namespace == "pbs"
    assert (tmp_path / "mapping.db").exists()
