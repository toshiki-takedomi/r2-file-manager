from __future__ import annotations

import json

import pytest

from r2_file_manager.config import ConfigStore


class MemorySecrets:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, profile_id: str) -> str | None:
        return self.values.get(profile_id)

    def set(self, profile_id: str, secret: str) -> None:
        self.values[profile_id] = secret

    def delete(self, profile_id: str) -> None:
        self.values.pop(profile_id, None)


def valid_values(**overrides):
    return {
        "name": "Personal R2",
        "account_id": "a" * 32,
        "access_key_id": "access-one",
        "public_url": "https://example.r2.dev",
        **overrides,
    }


def test_secret_is_only_written_to_secret_store(tmp_path):
    secrets = MemorySecrets()
    store = ConfigStore(tmp_path, secrets)

    saved = store.save(valid_values(), "super-secret")

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert "super-secret" not in store.path.read_text(encoding="utf-8")
    assert payload["secret_ref"] == saved.id
    assert secrets.get(saved.id) == "super-secret"


def test_access_key_change_requires_new_secret(tmp_path):
    store = ConfigStore(tmp_path, MemorySecrets())
    store.save(valid_values(), "old-secret")

    with pytest.raises(ValueError, match="Secret Access Key"):
        store.save(valid_values(access_key_id="access-two"), None)


def test_existing_secret_is_kept_when_non_secret_fields_change(tmp_path):
    secrets = MemorySecrets()
    store = ConfigStore(tmp_path, secrets)
    saved = store.save(valid_values(), "secret")

    updated = store.save(valid_values(name="Updated"), None)

    assert updated.id == saved.id
    assert secrets.get(saved.id) == "secret"


def test_metrics_token_is_only_written_to_secret_store(tmp_path):
    secrets = MemorySecrets()
    store = ConfigStore(tmp_path, secrets)

    saved = store.save(valid_values(), "secret", "metrics-token")

    raw_config = store.path.read_text(encoding="utf-8")
    assert "metrics-token" not in raw_config
    assert store.get_metrics_token(saved) == "metrics-token"
