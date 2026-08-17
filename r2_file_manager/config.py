from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import keyring
from keyring.errors import KeyringError

SERVICE_NAME = "R2 File Manager"
METRICS_TOKEN_SUFFIX = ":cloudflare-api-token"
ACCOUNT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class SecretStore(Protocol):
    def get(self, profile_id: str) -> str | None: ...
    def set(self, profile_id: str, secret: str) -> None: ...
    def delete(self, profile_id: str) -> None: ...


class KeyringSecretStore:
    def get(self, profile_id: str) -> str | None:
        try:
            return keyring.get_password(SERVICE_NAME, profile_id)
        except KeyringError as exc:
            raise RuntimeError("Windows資格情報マネージャーを読み取れませんでした。") from exc

    def set(self, profile_id: str, secret: str) -> None:
        try:
            keyring.set_password(SERVICE_NAME, profile_id, secret)
        except KeyringError as exc:
            raise RuntimeError(
                "Windows資格情報マネージャーへ保存できませんでした。平文では保存していません。"
            ) from exc

    def delete(self, profile_id: str) -> None:
        try:
            if keyring.get_password(SERVICE_NAME, profile_id) is not None:
                keyring.delete_password(SERVICE_NAME, profile_id)
        except KeyringError as exc:
            raise RuntimeError("Windows資格情報マネージャーから削除できませんでした。") from exc


@dataclass(frozen=True)
class ConnectionSettings:
    id: str
    name: str
    account_id: str
    access_key_id: str
    public_url: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> "ConnectionSettings":
        return cls(
            id=str(value.get("id") or uuid.uuid4()),
            name=str(value.get("name") or "Personal R2").strip(),
            account_id=str(value.get("account_id") or "").strip(),
            access_key_id=str(value.get("access_key_id") or "").strip(),
            public_url=str(value.get("public_url") or "").strip().rstrip("/"),
        )


def validate_settings(settings: ConnectionSettings) -> None:
    if not settings.name:
        raise ValueError("接続名を入力してください。")
    if not ACCOUNT_ID_PATTERN.fullmatch(settings.account_id):
        raise ValueError("Account IDは32文字の16進数で入力してください。")
    if not settings.access_key_id:
        raise ValueError("Access Key IDを入力してください。")
    if settings.public_url and not settings.public_url.startswith(("https://", "http://")):
        raise ValueError("Public URLはhttp://またはhttps://から入力してください。")


def default_data_dir() -> Path:
    override = os.environ.get("R2_FILE_MANAGER_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "R2 File Manager"


class ConfigStore:
    def __init__(self, data_dir: Path | None = None, secrets: SecretStore | None = None):
        self.data_dir = data_dir or default_data_dir()
        self.path = self.data_dir / "config.json"
        self.secrets = secrets or KeyringSecretStore()

    @staticmethod
    def environment_defaults() -> dict[str, str]:
        return {
            "name": "Personal R2",
            "account_id": os.environ.get("R2_ACCOUNT_ID", ""),
            "access_key_id": os.environ.get("R2_ACCESS_KEY", ""),
            "secret_access_key": os.environ.get("R2_SECRET_ACCESS_KEY", ""),
            "cloudflare_api_token": os.environ.get("CLOUDFLARE_API_TOKEN", ""),
            "public_url": os.environ.get("R2_PUBLIC_URL", ""),
        }

    def load(self) -> ConnectionSettings | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ConnectionSettings.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("接続設定ファイルを読み取れませんでした。") from exc

    def get_secret(self, settings: ConnectionSettings | None = None) -> str | None:
        current = settings or self.load()
        if current is None:
            return None
        return self.secrets.get(current.id)

    def get_metrics_token(self, settings: ConnectionSettings | None = None) -> str | None:
        current = settings or self.load()
        if current is None:
            return None
        return self.secrets.get(f"{current.id}{METRICS_TOKEN_SUFFIX}")

    def save(
        self,
        values: dict,
        secret: str | None,
        metrics_token: str | None = None,
    ) -> ConnectionSettings:
        existing = self.load()
        settings = ConnectionSettings.from_dict(
            {
                **values,
                "id": existing.id if existing else values.get("id") or str(uuid.uuid4()),
            }
        )
        validate_settings(settings)

        access_key_changed = existing and existing.access_key_id != settings.access_key_id
        if access_key_changed and not secret:
            raise ValueError("Access Key IDを変更する場合はSecret Access Keyも入力してください。")
        if not secret and (existing is None or not self.secrets.get(settings.id)):
            raise ValueError("Secret Access Keyを入力してください。")

        # The secret is persisted first. Roll it back if the atomic config update fails.
        previous_secret = self.secrets.get(settings.id)
        metrics_key = f"{settings.id}{METRICS_TOKEN_SUFFIX}"
        previous_metrics_token = self.secrets.get(metrics_key)
        if secret:
            self.secrets.set(settings.id, secret)
        if metrics_token:
            self.secrets.set(metrics_key, metrics_token)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(".tmp")
            payload = {**asdict(settings), "secret_ref": settings.id}
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, self.path)
        except OSError:
            if secret:
                if previous_secret is None:
                    self.secrets.delete(settings.id)
                else:
                    self.secrets.set(settings.id, previous_secret)
            if metrics_token:
                if previous_metrics_token is None:
                    self.secrets.delete(metrics_key)
                else:
                    self.secrets.set(metrics_key, previous_metrics_token)
            raise
        return settings
