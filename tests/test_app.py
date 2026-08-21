from __future__ import annotations

import re
import time

from r2_file_manager.app import create_app
from r2_file_manager.config import ConfigStore


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def get(self, profile_id):
        return self.values.get(profile_id)

    def set(self, profile_id, secret):
        self.values[profile_id] = secret

    def delete(self, profile_id):
        self.values.pop(profile_id, None)


class FakeClient:
    def list_buckets(self, **_kwargs):
        return {"Buckets": []}


class FakeService:
    last_credentials = None
    last_move = None

    def __init__(self, settings, secret):
        self.settings = settings
        self.secret = secret
        self.client = FakeClient()
        FakeService.last_credentials = (settings, secret)

    def test_connection(self):
        self.client.list_buckets()

    def list_buckets(self):
        return []

    def search_objects(self, bucket, query, continuation_token=None):
        return {
            "objects": [
                {
                    "key": f"archive/{query}.bin",
                    "name": f"{query}.bin",
                    "size": 42,
                    "etag": "etag",
                    "last_modified": None,
                    "storage_class": "STANDARD",
                }
            ],
            "next_token": continuation_token,
            "scanned": 1,
        }

    def download_info(self, bucket, key):
        return {"url": f"https://signed.example/{bucket}/{key}", "public": False, "expires_in": 3600}

    def download_url(self, bucket, key):
        return {
            "url": f"https://signed.example/download/{bucket}/{key}",
            "expires_in": 300,
            "file_name": key.rsplit("/", 1)[-1],
        }

    def move_object(
        self, bucket, source_key, destination_key, *, overwrite=False, progress_callback=None
    ):
        FakeService.last_move = (bucket, source_key, destination_key, overwrite)
        if progress_callback:
            progress_callback(0, 10)
            progress_callback(10, 10)
        return {"source_key": source_key, "destination_key": destination_key}


def make_client(tmp_path):
    store = ConfigStore(tmp_path, MemorySecrets())
    app = create_app(config_store=store, service_factory=FakeService)
    app.config["TESTING"] = True
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    token = re.search(r'<meta name="r2fm-token" content="([^"]+)">', html).group(1)
    return client, store, {"X-R2FM-Token": token}


def test_mutation_requires_request_token(tmp_path):
    client, _store, _headers = make_client(tmp_path)
    response = client.post("/api/settings/test", json={})
    assert response.status_code == 403


def test_read_api_also_requires_request_token(tmp_path):
    client, _store, _headers = make_client(tmp_path)
    response = client.get("/api/settings/environment")
    assert response.status_code == 403


def test_metrics_endpoint_is_optional(tmp_path):
    client, store, headers = make_client(tmp_path)
    store.save(
        {
            "name": "Test R2",
            "account_id": "a" * 32,
            "access_key_id": "access",
            "public_url": "",
        },
        "secret",
    )

    response = client.get("/api/metrics", headers=headers)

    assert response.status_code == 200
    assert response.get_json() == {"configured": False}
    assert response.cache_control.no_store


def test_download_info_endpoint(tmp_path):
    client, store, headers = make_client(tmp_path)
    store.save(
        {"name": "Test R2", "account_id": "a" * 32, "access_key_id": "access", "public_url": ""},
        "secret",
    )

    response = client.get(
        "/api/objects/download-info?bucket=models&key=checkpoints/model.bin", headers=headers
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "url": "https://signed.example/models/checkpoints/model.bin",
        "public": False,
        "expires_in": 3600,
    }
    assert response.cache_control.no_store


def test_download_url_endpoint(tmp_path):
    client, store, headers = make_client(tmp_path)
    store.save(
        {"name": "Test R2", "account_id": "a" * 32, "access_key_id": "access", "public_url": ""},
        "secret",
    )

    response = client.get(
        "/api/objects/download-url?bucket=models&key=checkpoints/model.bin", headers=headers
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "url": "https://signed.example/download/models/checkpoints/model.bin",
        "expires_in": 300,
        "file_name": "model.bin",
    }
    assert response.cache_control.no_store


def test_batch_download_info_endpoint(tmp_path):
    client, store, headers = make_client(tmp_path)
    store.save(
        {"name": "Test R2", "account_id": "a" * 32, "access_key_id": "access", "public_url": ""},
        "secret",
    )

    response = client.post(
        "/api/objects/download-info-batch",
        headers=headers,
        json={
            "bucket": "models",
            "keys": ["checkpoints/model.bin", "reports/result.txt"],
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "downloads": [
            {
                "key": "checkpoints/model.bin",
                "url": "https://signed.example/models/checkpoints/model.bin",
                "public": False,
                "expires_in": 3600,
            },
            {
                "key": "reports/result.txt",
                "url": "https://signed.example/models/reports/result.txt",
                "public": False,
                "expires_in": 3600,
            },
        ]
    }
    assert response.cache_control.no_store


def test_batch_download_info_rejects_empty_or_too_many_keys(tmp_path):
    client, store, headers = make_client(tmp_path)
    store.save(
        {"name": "Test R2", "account_id": "a" * 32, "access_key_id": "access", "public_url": ""},
        "secret",
    )

    empty = client.post(
        "/api/objects/download-info-batch",
        headers=headers,
        json={"bucket": "models", "keys": []},
    )
    too_many = client.post(
        "/api/objects/download-info-batch",
        headers=headers,
        json={"bucket": "models", "keys": [f"file-{index}" for index in range(501)]},
    )

    assert empty.status_code == 400
    assert too_many.status_code == 400


def test_object_search_endpoint(tmp_path):
    client, store, headers = make_client(tmp_path)
    store.save(
        {"name": "Test R2", "account_id": "a" * 32, "access_key_id": "access", "public_url": ""},
        "secret",
    )

    response = client.get(
        "/api/objects/search?bucket=models&query=AnImA&continuation_token=next-page",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "objects": [
            {
                "key": "archive/AnImA.bin",
                "name": "AnImA.bin",
                "size": 42,
                "etag": "etag",
                "last_modified": None,
                "storage_class": "STANDARD",
            }
        ],
        "next_token": "next-page",
        "scanned": 1,
    }


def test_object_search_rejects_empty_query(tmp_path):
    client, store, headers = make_client(tmp_path)
    store.save(
        {"name": "Test R2", "account_id": "a" * 32, "access_key_id": "access", "public_url": ""},
        "secret",
    )

    response = client.get("/api/objects/search?bucket=models&query=", headers=headers)

    assert response.status_code == 400


def test_move_object_endpoint(tmp_path):
    client, store, headers = make_client(tmp_path)
    store.save(
        {"name": "Test R2", "account_id": "a" * 32, "access_key_id": "access", "public_url": ""},
        "secret",
    )

    response = client.post(
        "/api/objects/move",
        headers=headers,
        json={
            "bucket": "models",
            "source_key": "incoming/model.bin",
            "destination_key": "archive/日本語.bin",
            "overwrite": True,
        },
    )

    assert response.status_code == 202
    job = response.get_json()
    assert job["source_key"] == "incoming/model.bin"
    assert job["destination_key"] == "archive/日本語.bin"
    assert job["status"] in {"queued", "moving", "complete"}

    for _attempt in range(100):
        progress = client.get(f"/api/moves/{job['id']}", headers=headers).get_json()
        if progress["status"] == "complete":
            break
        time.sleep(0.01)
    assert progress["status"] == "complete"
    assert progress["transferred_bytes"] == 10
    assert progress["total_bytes"] == 10

    listed = client.get("/api/moves", headers=headers).get_json()["moves"]
    assert any(item["id"] == job["id"] for item in listed)
    assert FakeService.last_move == (
        "models",
        "incoming/model.bin",
        "archive/日本語.bin",
        True,
    )


def test_settings_are_tested_then_saved_without_plaintext_secret(tmp_path):
    client, store, headers = make_client(tmp_path)
    response = client.post(
        "/api/settings",
        headers=headers,
        json={
            "name": "Test R2",
            "account_id": "a" * 32,
            "access_key_id": "access",
            "secret_access_key": "secret-value",
            "public_url": "",
        },
    )

    assert response.status_code == 200
    assert store.load().name == "Test R2"
    assert "secret-value" not in store.path.read_text(encoding="utf-8")
    assert FakeService.last_credentials[1] == "secret-value"
