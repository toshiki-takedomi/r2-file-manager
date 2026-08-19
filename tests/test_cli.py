from __future__ import annotations

import json

import pytest

from r2_file_manager import __main__ as cli


class FakeService:
    def __init__(self):
        self.calls = []

    def list_buckets(self):
        return [{"name": "assets", "created_at": "2026-08-20T01:02:03+00:00"}]

    def list_objects(self, bucket, prefix="", continuation_token=None, *, recursive=False):
        self.calls.append((bucket, prefix, continuation_token, recursive))
        if continuation_token is None:
            return {
                "folders": [] if recursive else [{"prefix": "images/", "name": "images"}],
                "objects": [
                    {
                        "key": f"{prefix}one.txt",
                        "name": "one.txt",
                        "size": 3,
                        "etag": "etag-1",
                        "last_modified": None,
                        "storage_class": "STANDARD",
                    }
                ],
                "next_token": "next",
            }
        return {
            "folders": [],
            "objects": [
                {
                    "key": f"{prefix}two.txt",
                    "name": "two.txt",
                    "size": 4,
                    "etag": "etag-2",
                    "last_modified": "2026-08-20T01:02:03+00:00",
                    "storage_class": "STANDARD",
                }
            ],
            "next_token": None,
        }


def test_help_lists_cli_commands(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "list-buckets" in output
    assert "list-objects" in output


def test_list_buckets_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_configured_service", lambda: FakeService())

    assert cli.main(["list-buckets", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == [
        {"name": "assets", "created_at": "2026-08-20T01:02:03+00:00"}
    ]


def test_list_objects_fetches_all_pages(monkeypatch, capsys):
    service = FakeService()
    monkeypatch.setattr(cli, "_configured_service", lambda: service)

    assert (
        cli.main(
            ["list-objects", "assets", "--prefix", "docs/", "--recursive", "--json"]
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert [item["key"] for item in result["objects"]] == ["docs/one.txt", "docs/two.txt"]
    assert service.calls == [
        ("assets", "docs/", None, True),
        ("assets", "docs/", "next", True),
    ]
