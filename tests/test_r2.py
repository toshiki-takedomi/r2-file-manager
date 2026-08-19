import math

import pytest

from r2_file_manager.config import ConnectionSettings
from r2_file_manager.r2 import MAX_PARTS, MIB, R2Service, choose_part_size, validate_bucket_name


class RecordingClient:
    def __init__(self):
        self.calls = []

    def list_buckets(self, **kwargs):
        self.calls.append(kwargs)
        return {"Buckets": []}


def test_connection_uses_plain_list_buckets_for_r2_compatibility():
    service = R2Service.__new__(R2Service)
    service.client = RecordingClient()

    service.test_connection()

    assert service.client.calls == [{}]


def make_settings(public_url=""):
    return ConnectionSettings(
        id="test", name="Test", account_id="a" * 32, access_key_id="access", public_url=public_url
    )


def test_download_info_uses_encoded_public_url_when_configured():
    service = R2Service.__new__(R2Service)
    service.settings = make_settings("https://cdn.example.com")

    result = service.download_info("bucket", "models/日本 語.zip")

    assert result == {
        "url": "https://cdn.example.com/models/%E6%97%A5%E6%9C%AC%20%E8%AA%9E.zip",
        "public": True,
        "expires_in": None,
    }


def test_download_info_creates_one_hour_presigned_url_without_public_url():
    class PresigningClient:
        def __init__(self):
            self.call = None

        def generate_presigned_url(self, operation, **kwargs):
            self.call = (operation, kwargs)
            return "https://signed.example/download"

    service = R2Service.__new__(R2Service)
    service.settings = make_settings()
    service.client = PresigningClient()

    result = service.download_info("bucket", "models/file.bin")

    assert result == {"url": "https://signed.example/download", "public": False, "expires_in": 3600}
    assert service.client.call == (
        "get_object",
        {"Params": {"Bucket": "bucket", "Key": "models/file.bin"}, "ExpiresIn": 3600},
    )


def test_download_url_forces_attachment_with_utf8_file_name():
    class PresigningClient:
        def __init__(self):
            self.call = None

        def generate_presigned_url(self, operation, **kwargs):
            self.call = (operation, kwargs)
            return "https://signed.example/download"

    service = R2Service.__new__(R2Service)
    service.client = PresigningClient()

    result = service.download_url("bucket", "models/日本 語.zip")

    assert result == {
        "url": "https://signed.example/download",
        "expires_in": 300,
        "file_name": "日本 語.zip",
    }
    assert service.client.call == (
        "get_object",
        {
            "Params": {
                "Bucket": "bucket",
                "Key": "models/日本 語.zip",
                "ResponseContentDisposition": (
                    'attachment; filename="download.zip"; '
                    "filename*=UTF-8''%E6%97%A5%E6%9C%AC%20%E8%AA%9E.zip"
                ),
            },
            "ExpiresIn": 300,
        },
    )


def test_default_part_size_for_multi_gigabyte_file():
    assert choose_part_size(8 * 1024**3) == 16 * MIB


def test_part_size_never_exceeds_maximum_part_count():
    size = 5 * 1024**4
    part_size = choose_part_size(size)
    assert part_size % MIB == 0
    assert math.ceil(size / part_size) <= MAX_PARTS


@pytest.mark.parametrize("name", ["ab", "UPPER", "-starts", "ends-", "has_underscore"])
def test_invalid_bucket_names(name):
    with pytest.raises(ValueError):
        validate_bucket_name(name)


@pytest.mark.parametrize("name", ["abc", "models-2026", "a" * 63])
def test_valid_bucket_names(name):
    validate_bucket_name(name)
