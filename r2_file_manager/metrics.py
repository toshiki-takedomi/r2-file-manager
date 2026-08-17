from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _metric_value(group: dict[str, Any], state: str, field: str) -> int:
    value = group.get(state) or {}
    try:
        return int(value.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def parse_account_metrics(result: dict[str, Any]) -> dict[str, Any]:
    classes = {
        "standard": result.get("standard") or {},
        "infrequent_access": result.get("infrequentAccess") or {},
    }
    breakdown = {}
    for name, group in classes.items():
        published_payload = _metric_value(group, "published", "payloadSize")
        published_metadata = _metric_value(group, "published", "metadataSize")
        uploaded_payload = _metric_value(group, "uploaded", "payloadSize")
        uploaded_metadata = _metric_value(group, "uploaded", "metadataSize")
        breakdown[name] = {
            "stored_bytes": published_payload + published_metadata,
            "payload_bytes": published_payload,
            "metadata_bytes": published_metadata,
            "objects": _metric_value(group, "published", "objects"),
            "uploading_bytes": uploaded_payload + uploaded_metadata,
        }
    return {
        "stored_bytes": sum(item["stored_bytes"] for item in breakdown.values()),
        "payload_bytes": sum(item["payload_bytes"] for item in breakdown.values()),
        "metadata_bytes": sum(item["metadata_bytes"] for item in breakdown.values()),
        "objects": sum(item["objects"] for item in breakdown.values()),
        "uploading_bytes": sum(item["uploading_bytes"] for item in breakdown.values()),
        "storage_classes": breakdown,
    }


def fetch_account_metrics(account_id: str, api_token: str) -> dict[str, Any]:
    request = Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/metrics",
        headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed Cloudflare endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("Cloudflare API Tokenが無効か、R2の読み取り権限がありません。") from exc
        raise RuntimeError(f"使用量APIがエラーを返しました（HTTP {exc.code}）。") from exc
    except URLError as exc:
        raise RuntimeError("Cloudflare使用量APIへ接続できませんでした。") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("Cloudflare使用量APIの応答を読み取れませんでした。") from exc

    if not payload.get("success") or not isinstance(payload.get("result"), dict):
        errors = payload.get("errors") or []
        message = errors[0].get("message") if errors and isinstance(errors[0], dict) else None
        raise RuntimeError(message or "Cloudflare使用量APIから正常な応答がありませんでした。")
    return parse_account_metrics(payload["result"])

