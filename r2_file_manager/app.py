from __future__ import annotations

import math
import secrets
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, render_template, request

from .config import ConfigStore, ConnectionSettings, validate_settings
from .metrics import fetch_account_metrics
from .r2 import R2Service
from .uploads import UploadRegistry


def _json() -> dict[str, Any]:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("JSON形式のリクエストが必要です。")
    return value


def _safe_cloud_error(exc: ClientError) -> tuple[str, int]:
    error = exc.response.get("Error", {})
    code = str(error.get("Code") or "R2Error")
    status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 502)
    known = {
        "AccessDenied": "アクセスが拒否されました。APIトークンの権限を確認してください。",
        "InvalidAccessKeyId": "Access Key IDが正しくありません。",
        "SignatureDoesNotMatch": "Secret Access Keyが正しくありません。",
        "NoSuchBucket": "バケットが見つかりません。",
        "BucketNotEmpty": "ファイルが存在するバケットは削除できません。",
        "BucketAlreadyExists": "同名のバケットがすでに存在します。",
        "BucketAlreadyOwnedByYou": "同名のバケットがすでに存在します。",
        "EntityTooLarge": "R2のアップロードサイズ上限を超えています。",
        "InvalidPart": "アップロード済みパートを確認できませんでした。再試行してください。",
        "NotImplemented": "このリクエスト形式はR2でサポートされていません。アプリを最新版へ更新してください。",
    }
    return known.get(code, f"R2 APIエラーが発生しました（{code}）。"), min(max(status, 400), 599)


def create_app(
    *,
    config_store: ConfigStore | None = None,
    service_factory: type[R2Service] = R2Service,
    metrics_fetcher=fetch_account_metrics,
    data_dir: Path | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.update(JSON_AS_ASCII=False)
    store = config_store or ConfigStore(data_dir=data_dir)
    registry = UploadRegistry(store.data_dir / "uploads.json")
    api_token = secrets.token_urlsafe(32)
    move_jobs: dict[str, dict[str, Any]] = {}
    move_jobs_lock = threading.Lock()

    def move_job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: job[key]
            for key in (
                "id",
                "bucket",
                "source_key",
                "destination_key",
                "status",
                "transferred_bytes",
                "total_bytes",
                "error",
            )
        }

    def require_token() -> None:
        if request.headers.get("X-R2FM-Token") != api_token:
            raise PermissionError("無効なリクエストです。画面を再読み込みしてください。")

    @app.before_request
    def protect_local_api():
        # The per-process token also protects read APIs such as environment imports.
        if request.path.startswith("/api/"):
            require_token()

    def current_service() -> R2Service:
        settings = store.load()
        if settings is None:
            raise RuntimeError("接続設定を完了してください。")
        secret = store.get_secret(settings)
        if not secret:
            raise RuntimeError("Secret Access Keyが保存されていません。")
        return service_factory(settings, secret)

    @app.get("/")
    def index():
        return render_template("index.html", api_token=api_token)

    @app.get("/api/settings")
    def get_settings():
        settings = store.load()
        if settings is None:
            defaults = store.environment_defaults()
            return jsonify(
                configured=False,
                settings={
                    key: value
                    for key, value in defaults.items()
                    if key not in {"secret_access_key", "cloudflare_api_token"}
                },
                secret_access_key=defaults["secret_access_key"],
                secret_configured=False,
                cloudflare_api_token=defaults["cloudflare_api_token"],
                metrics_token_configured=False,
            )
        return jsonify(
            configured=True,
            settings={
                "name": settings.name,
                "account_id": settings.account_id,
                "access_key_id": settings.access_key_id,
                "public_url": settings.public_url,
            },
            secret_access_key="",
            secret_configured=bool(store.get_secret(settings)),
            cloudflare_api_token="",
            metrics_token_configured=bool(store.get_metrics_token(settings)),
        )

    @app.get("/api/settings/environment")
    def environment_settings():
        return jsonify(store.environment_defaults())

    def proposed_connection(values: dict[str, Any]) -> tuple[ConnectionSettings, str]:
        existing = store.load()
        settings = ConnectionSettings.from_dict(
            {**values, "id": existing.id if existing else values.get("id")}
        )
        validate_settings(settings)
        submitted_secret = str(values.get("secret_access_key") or "")
        identity_changed = bool(
            existing
            and (
                existing.account_id != settings.account_id
                or existing.access_key_id != settings.access_key_id
            )
        )
        if identity_changed and not submitted_secret:
            raise ValueError("Account IDまたはAccess Key IDを変更する場合はSecretも入力してください。")
        secret_value = submitted_secret or (store.get_secret(existing) if existing else None)
        if not secret_value:
            raise ValueError("Secret Access Keyを入力してください。")
        return settings, secret_value

    @app.post("/api/settings/test")
    def test_settings():
        require_token()
        values = _json()
        settings, secret_value = proposed_connection(values)
        service_factory(settings, secret_value).test_connection()
        submitted_metrics_token = str(values.get("cloudflare_api_token") or "").strip()
        if submitted_metrics_token:
            metrics_fetcher(settings.account_id, submitted_metrics_token)
        return jsonify(ok=True)

    @app.post("/api/settings")
    def save_settings():
        require_token()
        values = _json()
        settings, secret_value = proposed_connection(values)
        service_factory(settings, secret_value).test_connection()
        submitted_metrics_token = str(values.get("cloudflare_api_token") or "").strip()
        if submitted_metrics_token:
            metrics_fetcher(settings.account_id, submitted_metrics_token)
        submitted_secret = str(values.get("secret_access_key") or "") or None
        saved = store.save(values, submitted_secret, submitted_metrics_token or None)
        return jsonify(ok=True, name=saved.name)

    @app.get("/api/metrics")
    def account_metrics():
        settings = store.load()
        if settings is None:
            raise RuntimeError("接続設定を完了してください。")
        metrics_token = store.get_metrics_token(settings)
        if not metrics_token:
            response = jsonify(configured=False)
        else:
            response = jsonify(
                configured=True,
                metrics=metrics_fetcher(settings.account_id, metrics_token),
            )
        response.cache_control.no_store = True
        return response

    @app.get("/api/buckets")
    def list_buckets():
        return jsonify(buckets=current_service().list_buckets())

    @app.post("/api/buckets")
    def create_bucket():
        require_token()
        name = str(_json().get("name") or "").strip()
        current_service().create_bucket(name)
        return jsonify(ok=True), 201

    @app.delete("/api/buckets/<name>")
    def delete_bucket(name: str):
        require_token()
        current_service().delete_bucket(name)
        return jsonify(ok=True)

    @app.get("/api/objects")
    def list_objects():
        bucket = request.args.get("bucket", "")
        if not bucket:
            raise ValueError("バケットを指定してください。")
        prefix = request.args.get("prefix", "")
        token = request.args.get("continuation_token") or None
        return jsonify(current_service().list_objects(bucket, prefix, token))

    @app.get("/api/objects/download-info")
    def object_download_info():
        bucket = request.args.get("bucket", "")
        key = request.args.get("key", "")
        if not bucket or not key:
            raise ValueError("ダウンロード対象が正しくありません。")
        response = jsonify(current_service().download_info(bucket, key))
        response.cache_control.no_store = True
        return response

    @app.get("/api/objects/download-url")
    def object_download_url():
        bucket = request.args.get("bucket", "")
        key = request.args.get("key", "")
        if not bucket or not key:
            raise ValueError("ダウンロード対象が正しくありません。")
        response = jsonify(current_service().download_url(bucket, key))
        response.cache_control.no_store = True
        return response

    @app.post("/api/objects/download-info-batch")
    def object_download_info_batch():
        values = _json()
        bucket = str(values.get("bucket") or "")
        keys = values.get("keys")
        if (
            not bucket
            or not isinstance(keys, list)
            or not keys
            or not all(isinstance(key, str) and key for key in keys)
        ):
            raise ValueError("ダウンロード対象が正しくありません。")
        if len(keys) > 500:
            raise ValueError("一度に生成できるダウンロードURLは500件までです。")
        if any(len(key.encode("utf-8")) > 1024 for key in keys):
            raise ValueError("オブジェクト名が1,024バイトを超えています。")

        service = current_service()
        downloads = [{"key": key, **service.download_info(bucket, key)} for key in keys]
        response = jsonify(downloads=downloads)
        response.cache_control.no_store = True
        return response

    @app.post("/api/objects/move")
    def move_object():
        values = _json()
        bucket = str(values.get("bucket") or "")
        source_key = str(values.get("source_key") or "")
        destination_key = str(values.get("destination_key") or "")
        if not bucket or not source_key or not destination_key:
            raise ValueError("移動対象が正しくありません。")
        service = current_service()
        job_id = secrets.token_urlsafe(16)
        job: dict[str, Any] = {
            "id": job_id,
            "bucket": bucket,
            "source_key": source_key,
            "destination_key": destination_key,
            "status": "queued",
            "transferred_bytes": 0,
            "total_bytes": 0,
            "error": "",
        }
        with move_jobs_lock:
            for old_id in list(move_jobs):
                if len(move_jobs) < 100:
                    break
                if move_jobs[old_id]["status"] in {"complete", "failed"}:
                    del move_jobs[old_id]
            move_jobs[job_id] = job

        def update_progress(transferred: int, total: int) -> None:
            with move_jobs_lock:
                job["transferred_bytes"] = transferred
                job["total_bytes"] = total

        def run_move() -> None:
            with move_jobs_lock:
                job["status"] = "moving"
            try:
                service.move_object(
                    bucket,
                    source_key,
                    destination_key,
                    overwrite=values.get("overwrite") is True,
                    progress_callback=update_progress,
                )
            except ValueError as exc:
                message = str(exc)
            except ClientError as exc:
                message, _status = _safe_cloud_error(exc)
            except BotoCoreError:
                message = "R2へ接続できませんでした。ネットワーク接続を確認してください。"
            except Exception:
                message = "ファイルの移動に失敗しました。"
            else:
                with move_jobs_lock:
                    job["status"] = "complete"
                    job["transferred_bytes"] = job["total_bytes"]
                return
            with move_jobs_lock:
                job["status"] = "failed"
                job["error"] = message

        threading.Thread(target=run_move, name=f"r2-move-{job_id}", daemon=True).start()
        return jsonify(move_job_snapshot(job)), 202

    @app.get("/api/moves")
    def list_move_jobs():
        with move_jobs_lock:
            return jsonify(moves=[move_job_snapshot(job) for job in move_jobs.values()])

    @app.get("/api/moves/<job_id>")
    def get_move_job(job_id: str):
        with move_jobs_lock:
            job = move_jobs.get(job_id)
            if job is None:
                raise KeyError("移動状況が見つかりません。")
            return jsonify(move_job_snapshot(job))

    @app.post("/api/objects/delete")
    def delete_objects():
        require_token()
        values = _json()
        bucket = str(values.get("bucket") or "")
        keys = values.get("keys")
        if not bucket or not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValueError("削除対象が正しくありません。")
        return jsonify(current_service().delete_objects(bucket, keys))

    @app.get("/api/uploads")
    def list_uploads():
        return jsonify(uploads=registry.list())

    @app.post("/api/uploads")
    def begin_upload():
        require_token()
        values = _json()
        bucket = str(values.get("bucket") or "")
        key = str(values.get("key") or "")
        file_name = str(values.get("file_name") or key.rsplit("/", 1)[-1])
        content_type = str(values.get("content_type") or "application/octet-stream")
        overwrite = bool(values.get("overwrite"))
        try:
            size = int(values.get("size"))
        except (TypeError, ValueError) as exc:
            raise ValueError("ファイルサイズが正しくありません。") from exc
        if not bucket or not key or size < 0:
            raise ValueError("アップロード情報が正しくありません。")
        if len(key.encode("utf-8")) > 1024:
            raise ValueError("オブジェクト名が1,024バイトを超えています。")
        service = current_service()
        if not overwrite and service.object_exists(bucket, key):
            return jsonify(error="同名のファイルが存在します。", code="OBJECT_EXISTS"), 409
        upload_id = None
        if size > 0:
            response = service.client.create_multipart_upload(
                Bucket=bucket,
                Key=key,
                ContentType=content_type,
            )
            upload_id = response["UploadId"]
        session = registry.create(
            bucket=bucket,
            key=key,
            file_name=file_name,
            size=size,
            content_type=content_type,
            upload_id=upload_id,
        )
        return jsonify(session), 201

    @app.put("/api/uploads/<session_id>/parts/<int:part_number>")
    def upload_part(session_id: str, part_number: int):
        require_token()
        session = registry.get(session_id)
        if session is None:
            raise KeyError("アップロードセッションが見つかりません。")
        if not session["upload_id"]:
            raise ValueError("空ファイルにはパートを送信できません。")
        expected_parts = math.ceil(session["size"] / session["part_size"])
        if part_number < 1 or part_number > expected_parts:
            raise ValueError("パート番号が正しくありません。")
        offset = (part_number - 1) * session["part_size"]
        expected_size = min(session["part_size"], session["size"] - offset)
        if request.content_length != expected_size:
            raise ValueError("パートサイズが一致しません。")

        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as body:
            shutil.copyfileobj(request.stream, body, length=1024 * 1024)
            actual_size = body.tell()
            if actual_size != expected_size:
                raise ValueError("パートの受信が完了しませんでした。再試行してください。")
            body.seek(0)
            response = current_service().client.upload_part(
                Bucket=session["bucket"],
                Key=session["key"],
                UploadId=session["upload_id"],
                PartNumber=part_number,
                Body=body,
                ContentLength=actual_size,
            )
        registry.record_part(session_id, part_number, response["ETag"])
        return jsonify(ok=True, part_number=part_number)

    @app.post("/api/uploads/<session_id>/complete")
    def complete_upload(session_id: str):
        require_token()
        session = registry.get(session_id)
        if session is None:
            raise KeyError("アップロードセッションが見つかりません。")
        service = current_service()
        if session["size"] == 0:
            service.client.put_object(
                Bucket=session["bucket"],
                Key=session["key"],
                Body=b"",
                ContentType=session["content_type"],
            )
        else:
            expected_parts = math.ceil(session["size"] / session["part_size"])
            if len(session["parts"]) != expected_parts:
                raise ValueError("未送信のパートがあります。")
            parts = [
                {"PartNumber": number, "ETag": session["parts"][str(number)]}
                for number in range(1, expected_parts + 1)
            ]
            service.client.complete_multipart_upload(
                Bucket=session["bucket"],
                Key=session["key"],
                UploadId=session["upload_id"],
                MultipartUpload={"Parts": parts},
            )
        registry.remove(session_id)
        return jsonify(ok=True)

    @app.delete("/api/uploads/<session_id>")
    def abort_upload(session_id: str):
        require_token()
        session = registry.get(session_id)
        if session is None:
            return jsonify(ok=True)
        if session.get("upload_id"):
            current_service().client.abort_multipart_upload(
                Bucket=session["bucket"],
                Key=session["key"],
                UploadId=session["upload_id"],
            )
        registry.remove(session_id)
        return jsonify(ok=True)

    @app.errorhandler(ValueError)
    def value_error(exc: ValueError):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(KeyError)
    def key_error(exc: KeyError):
        return jsonify(error=str(exc.args[0] if exc.args else exc)), 404

    @app.errorhandler(PermissionError)
    def permission_error(exc: PermissionError):
        return jsonify(error=str(exc)), 403

    @app.errorhandler(ClientError)
    def client_error(exc: ClientError):
        message, status = _safe_cloud_error(exc)
        return jsonify(error=message), status

    @app.errorhandler(BotoCoreError)
    def boto_error(_exc: BotoCoreError):
        return jsonify(error="R2へ接続できませんでした。ネットワーク接続を確認してください。"), 502

    @app.errorhandler(RuntimeError)
    def runtime_error(exc: RuntimeError):
        return jsonify(error=str(exc)), 500

    return app
