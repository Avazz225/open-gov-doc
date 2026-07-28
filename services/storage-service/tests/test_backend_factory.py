import pytest
from pydantic import ValidationError
from storage_service.backends import LocalFilesystemBackend, build_backends, resolve_targets
from storage_service.settings import BackendTargetConfig, Settings


def test_resolve_targets_preserves_order(tmp_path):
    settings = Settings(
        targets=[
            BackendTargetConfig(id="primary", type="local", base_path=str(tmp_path / "a")),
            BackendTargetConfig(id="secondary", type="local", base_path=str(tmp_path / "b")),
        ]
    )

    assert resolve_targets(settings) == ["primary", "secondary"]


def test_build_backends_supports_multiple_instances_of_the_same_type(tmp_path):
    """P5b-S6 "Mehrfach-Devices" (3.6): beliebig viele gleichartige
    Backend-Instanzen im selben Ziel-Set, z. B. zwei unabhängige `local`-Ziele
    (stünde für zwei unabhängige NFS-Mounts) mit eigenen Pfaden."""
    settings = Settings(
        targets=[
            BackendTargetConfig(id="local-a", type="local", base_path=str(tmp_path / "a")),
            BackendTargetConfig(id="local-b", type="local", base_path=str(tmp_path / "b")),
        ]
    )

    backends = build_backends(settings)

    assert set(backends.keys()) == {"local-a", "local-b"}
    assert isinstance(backends["local-a"], LocalFilesystemBackend)
    assert isinstance(backends["local-b"], LocalFilesystemBackend)
    assert (tmp_path / "a").is_dir()
    assert (tmp_path / "b").is_dir()


async def test_backends_with_same_type_are_independent(tmp_path):
    settings = Settings(
        targets=[
            BackendTargetConfig(id="local-a", type="local", base_path=str(tmp_path / "a")),
            BackendTargetConfig(id="local-b", type="local", base_path=str(tmp_path / "b")),
        ]
    )
    backends = build_backends(settings)

    await backends["local-a"].write("file.txt", b"nur in a")

    assert await backends["local-a"].exists("file.txt")
    assert not await backends["local-b"].exists("file.txt")


def test_backend_target_config_requires_base_path_for_local():
    with pytest.raises(ValidationError, match="base_path"):
        BackendTargetConfig(id="x", type="local")


def test_backend_target_config_requires_all_s3_fields():
    with pytest.raises(ValidationError, match="endpoint_url"):
        BackendTargetConfig(id="x", type="s3", bucket="only-bucket-set")


def test_settings_rejects_duplicate_target_ids_in_practice_via_dict_collapse(tmp_path):
    """`build_backends()` selbst dedupliziert (Dict-Semantik) - die
    tatsächliche Eindeutigkeitsprüfung sitzt in `main._validate_settings()`
    (siehe test_api.py), hier nur die Grundlage dafür bestätigt: zwei Ziele
    mit derselben `id` würden sonst still eines überschreiben."""
    settings = Settings(
        targets=[
            BackendTargetConfig(id="dup", type="local", base_path=str(tmp_path / "a")),
            BackendTargetConfig(id="dup", type="local", base_path=str(tmp_path / "b")),
        ]
    )

    assert len(build_backends(settings)) == 1
