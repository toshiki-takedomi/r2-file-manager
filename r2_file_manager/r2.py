from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

import boto3
from botocore.config import Config

from .config import ConnectionSettings

MIB = 1024 * 1024
MIN_PART_SIZE = 5 * MIB
DEFAULT_PART_SIZE = 16 * MIB
MAX_PARTS = 10_000
BUCKET_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")


def choose_part_size(file_size: int) -> int:
    """Return a uniform MiB-aligned part size that stays under 10,000 parts."""
    required = max(DEFAULT_PART_SIZE, math.ceil(max(file_size, 1) / MAX_PARTS))
    return math.ceil(required / MIB) * MIB


def validate_bucket_name(name: str) -> None:
    if not BUCKET_PATTERN.fullmatch(name):
        raise ValueError("バケット名は3～63文字の小文字、数字、ハイフンで入力してください。")


def serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class R2Service:
    def __init__(self, settings: ConnectionSettings, secret: str):
        self.settings = settings
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=secret,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=15,
                read_timeout=120,
                max_pool_connections=16,
            ),
        )

    def test_connection(self) -> None:
        # R2's pagination extension uses `max-keys`, while boto3's MaxBuckets
        # serializes the AWS-specific `max-buckets` parameter. A plain request
        # is the most broadly compatible authentication/permission check.
        self.client.list_buckets()

    def list_buckets(self) -> list[dict[str, Any]]:
        response = self.client.list_buckets()
        return [
            {"name": item["Name"], "created_at": serialize_datetime(item.get("CreationDate"))}
            for item in response.get("Buckets", [])
        ]

    def create_bucket(self, name: str) -> None:
        validate_bucket_name(name)
        self.client.create_bucket(Bucket=name)

    def delete_bucket(self, name: str) -> None:
        probe = self.client.list_objects_v2(Bucket=name, MaxKeys=1)
        if probe.get("KeyCount", 0):
            raise ValueError("ファイルが存在するバケットは削除できません。")
        self.client.delete_bucket(Bucket=name)

    def list_objects(
        self, bucket: str, prefix: str = "", continuation_token: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": prefix,
            "Delimiter": "/",
            "MaxKeys": 500,
        }
        if continuation_token:
            params["ContinuationToken"] = continuation_token
        response = self.client.list_objects_v2(**params)
        folders = [item["Prefix"] for item in response.get("CommonPrefixes", [])]
        objects = []
        for item in response.get("Contents", []):
            key = item["Key"]
            if key == prefix or key.endswith("/"):
                continue
            objects.append(
                {
                    "key": key,
                    "name": key[len(prefix) :],
                    "size": item.get("Size", 0),
                    "etag": item.get("ETag", "").strip('"'),
                    "last_modified": serialize_datetime(item.get("LastModified")),
                    "storage_class": item.get("StorageClass", "STANDARD"),
                }
            )
        return {
            "folders": [{"prefix": value, "name": value[len(prefix) :].rstrip("/")} for value in folders],
            "objects": objects,
            "next_token": response.get("NextContinuationToken"),
        }

    def object_exists(self, bucket: str, key: str) -> bool:
        try:
            self.client.head_object(Bucket=bucket, Key=key)
            return True
        except self.client.exceptions.ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = exc.response.get("Error", {}).get("Code")
            if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def download_info(self, bucket: str, key: str, expires_in: int = 3600) -> dict[str, Any]:
        if self.settings.public_url:
            return {
                "url": f"{self.settings.public_url}/{quote(key, safe='/')}",
                "public": True,
                "expires_in": None,
            }
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return {"url": url, "public": False, "expires_in": expires_in}

    def download_url(self, bucket: str, key: str, expires_in: int = 300) -> dict[str, Any]:
        file_name = key.rsplit("/", 1)[-1].replace("\r", "").replace("\n", "") or "download"
        ascii_name = file_name.encode("ascii", "ignore").decode("ascii").strip()
        ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
        if not ascii_name or ascii_name.startswith("."):
            suffix = ascii_name if re.fullmatch(r"\.[A-Za-z0-9._-]{1,20}", ascii_name) else ""
            ascii_name = f"download{suffix}"
        disposition = (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(file_name, safe='')}"
        )
        url = self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentDisposition": disposition,
            },
            ExpiresIn=expires_in,
        )
        return {"url": url, "expires_in": expires_in, "file_name": file_name}

    def delete_objects(self, bucket: str, keys: list[str]) -> dict[str, Any]:
        if not keys:
            raise ValueError("削除するファイルを選択してください。")
        if len(keys) > 1000:
            raise ValueError("一度に削除できるのは1,000件までです。")
        response = self.client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in keys], "Quiet": False},
        )
        return {
            "deleted": [item["Key"] for item in response.get("Deleted", [])],
            "errors": response.get("Errors", []),
        }
