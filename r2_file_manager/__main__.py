from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import webbrowser
from collections.abc import Sequence
from typing import Any

from waitress import serve

from .app import create_app
from .config import ConfigStore, ConnectionSettings, validate_settings
from .r2 import R2Service


def available_port(preferred: int = 8877) -> int:
    configured = os.environ.get("R2_FILE_MANAGER_PORT")
    if configured:
        port = int(configured)
        if not 1 <= port <= 65535:
            raise ValueError("R2_FILE_MANAGER_PORTは1～65535で指定してください。")
        return port
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.05)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("利用可能なローカルポートが見つかりません。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="r2-file-manager",
        description="Cloudflare R2をWeb画面またはコマンドラインから操作します。",
        epilog="サブコマンドを省略するとWeb画面を起動します。",
    )
    subparsers = parser.add_subparsers(dest="command", title="サブコマンド")

    subparsers.add_parser("serve", help="ローカルWeb画面を起動します（既定）")

    buckets = subparsers.add_parser("list-buckets", help="バケット一覧を取得します")
    buckets.add_argument("--json", action="store_true", help="JSON形式で出力します")

    objects = subparsers.add_parser(
        "list-objects", help="バケット内のオブジェクトを一覧表示します"
    )
    objects.add_argument("bucket", help="対象のバケット名")
    objects.add_argument("--prefix", default="", help="対象を絞り込むキープレフィックス")
    objects.add_argument(
        "--recursive", action="store_true", help="サブフォルダー内のオブジェクトも再帰的に表示します"
    )
    objects.add_argument("--json", action="store_true", help="JSON形式で出力します")
    return parser


def _configured_service(
    store: ConfigStore | None = None,
    service_factory: type[R2Service] = R2Service,
) -> R2Service:
    config_store = store or ConfigStore()
    settings = config_store.load()
    if settings is not None:
        secret = config_store.get_secret(settings)
        if not secret:
            raise RuntimeError("Secret Access Keyが保存されていません。")
        return service_factory(settings, secret)

    defaults = config_store.environment_defaults()
    settings = ConnectionSettings.from_dict(defaults)
    try:
        validate_settings(settings)
    except ValueError as exc:
        raise RuntimeError(
            "接続設定がありません。Web画面で設定するか、R2_ACCOUNT_ID、"
            "R2_ACCESS_KEY、R2_SECRET_ACCESS_KEYを設定してください。"
        ) from exc
    secret = defaults["secret_access_key"]
    if not secret:
        raise RuntimeError(
            "接続設定がありません。Web画面で設定するか、R2_ACCOUNT_ID、"
            "R2_ACCESS_KEY、R2_SECRET_ACCESS_KEYを設定してください。"
        )
    return service_factory(settings, secret)


def _print_buckets(items: list[dict[str, Any]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    print("NAME\tCREATED_AT")
    for item in items:
        print(f"{item['name']}\t{item.get('created_at') or '-'}")


def _list_all_objects(
    service: R2Service, bucket: str, prefix: str, *, recursive: bool
) -> dict[str, Any]:
    folders: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        page = service.list_objects(bucket, prefix, token, recursive=recursive)
        folders.extend(page["folders"])
        objects.extend(page["objects"])
        token = page.get("next_token")
        if not token:
            break
    return {"folders": folders, "objects": objects}


def _print_objects(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("TYPE\tSIZE\tLAST_MODIFIED\tKEY")
    for folder in result["folders"]:
        print(f"folder\t-\t-\t{folder['prefix']}")
    for item in result["objects"]:
        print(
            f"object\t{item['size']}\t{item.get('last_modified') or '-'}\t{item['key']}"
        )


def serve_web() -> None:
    host = "127.0.0.1"
    port = available_port()
    app = create_app()
    threading.Timer(0.8, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"R2 File Manager: http://{host}:{port}")
    serve(app, host=host, port=port, threads=12)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {None, "serve"}:
        serve_web()
        return 0

    try:
        service = _configured_service()
        if args.command == "list-buckets":
            _print_buckets(service.list_buckets(), as_json=args.json)
        elif args.command == "list-objects":
            result = _list_all_objects(
                service, args.bucket, args.prefix, recursive=args.recursive
            )
            _print_objects(result, as_json=args.json)
    except Exception as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
