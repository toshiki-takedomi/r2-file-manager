from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .r2 import choose_part_size


class UploadRegistry:
    """Thread-safe, crash-tolerant metadata for incomplete multipart uploads."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, sessions: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._read().values())

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._read().get(session_id)

    def create(
        self,
        *,
        bucket: str,
        key: str,
        file_name: str,
        size: int,
        content_type: str,
        upload_id: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            sessions = self._read()
            session_id = str(uuid.uuid4())
            part_size = choose_part_size(size)
            session = {
                "id": session_id,
                "bucket": bucket,
                "key": key,
                "file_name": file_name,
                "size": size,
                "content_type": content_type or "application/octet-stream",
                "upload_id": upload_id,
                "part_size": part_size,
                "parts": {},
                "created_at": datetime.now(UTC).isoformat(),
            }
            sessions[session_id] = session
            self._write(sessions)
            return session

    def record_part(self, session_id: str, part_number: int, etag: str) -> dict[str, Any]:
        with self._lock:
            sessions = self._read()
            session = sessions.get(session_id)
            if session is None:
                raise KeyError("アップロードセッションが見つかりません。")
            session["parts"][str(part_number)] = etag
            self._write(sessions)
            return session

    def remove(self, session_id: str) -> None:
        with self._lock:
            sessions = self._read()
            sessions.pop(session_id, None)
            self._write(sessions)

