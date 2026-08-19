import math
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

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


class MoveClient:
    exceptions = SimpleNamespace(ClientError=ClientError)

    def __init__(self, destination_exists=False):
        self.destination_exists = destination_exists
        self.calls = []
        self.head_count = 0

    def head_object(self, **kwargs):
        self.calls.append(("head_object", kwargs))
        self.head_count += 1
        if self.head_count == 1:
            return {"ContentLength": 10}
        if self.destination_exists:
            return {"ContentLength": 10}
        raise ClientError(
            {
                "Error": {"Code": "NoSuchKey"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadObject",
        )

    def copy(self, source, bucket, key, Callback=None):
        self.calls.append(("copy", source, bucket, key))
        if Callback:
            Callback(4)
            Callback(-2)
            Callback(2)
            Callback(6)

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))


def test_move_object_copies_before_deleting_source():
    service = R2Service.__new__(R2Service)
    service.client = MoveClient()

    result = service.move_object("bucket", "incoming/file.bin", "archive/日本語.bin")

    assert result == {
        "source_key": "incoming/file.bin",
        "destination_key": "archive/日本語.bin",
    }
    assert service.client.calls == [
        ("head_object", {"Bucket": "bucket", "Key": "incoming/file.bin"}),
        ("head_object", {"Bucket": "bucket", "Key": "archive/日本語.bin"}),
        ("copy", {"Bucket": "bucket", "Key": "incoming/file.bin"}, "bucket", "archive/日本語.bin"),
        ("delete_object", {"Bucket": "bucket", "Key": "incoming/file.bin"}),
    ]


def test_move_object_keeps_source_when_copy_fails():
    class FailingCopyClient(MoveClient):
        def copy(self, source, bucket, key, Callback=None):
            self.calls.append(("copy", source, bucket, key))
            raise RuntimeError("copy failed")

    service = R2Service.__new__(R2Service)
    service.client = FailingCopyClient()

    with pytest.raises(RuntimeError, match="copy failed"):
        service.move_object("bucket", "incoming/file.bin", "archive/file.bin")

    assert [call[0] for call in service.client.calls] == ["head_object", "head_object", "copy"]


def test_move_object_does_not_overwrite_destination_by_default():
    service = R2Service.__new__(R2Service)
    service.client = MoveClient(destination_exists=True)

    with pytest.raises(ValueError, match="同名"):
        service.move_object("bucket", "incoming/file.bin", "archive/file.bin")

    assert [call[0] for call in service.client.calls] == ["head_object", "head_object"]


def test_move_object_can_explicitly_overwrite_destination():
    service = R2Service.__new__(R2Service)
    service.client = MoveClient(destination_exists=True)

    service.move_object("bucket", "old.bin", "new.bin", overwrite=True)

    assert [call[0] for call in service.client.calls] == ["head_object", "copy", "delete_object"]


def test_move_object_reports_copy_progress():
    service = R2Service.__new__(R2Service)
    service.client = MoveClient()
    progress = []

    service.move_object(
        "bucket",
        "source.bin",
        "destination.bin",
        progress_callback=lambda copied, total: progress.append((copied, total)),
    )

    assert progress == [(0, 10), (4, 10), (2, 10), (4, 10), (10, 10), (10, 10)]


@pytest.mark.parametrize(
    ("source", "destination", "message"),
    [
        ("same.bin", "same.bin", "異なるパス"),
        ("source.bin", "folder/", "ファイル名"),
        ("source.bin", "あ" * 513, "1,024バイト"),
    ],
)
def test_move_object_rejects_invalid_destination(source, destination, message):
    service = R2Service.__new__(R2Service)
    service.client = MoveClient()

    with pytest.raises(ValueError, match=message):
        service.move_object("bucket", source, destination)


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
