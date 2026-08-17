from r2_file_manager.uploads import UploadRegistry


def test_upload_registry_survives_new_instance(tmp_path):
    path = tmp_path / "uploads.json"
    first = UploadRegistry(path)
    session = first.create(
        bucket="bucket",
        key="models/file.bin",
        file_name="file.bin",
        size=20 * 1024 * 1024,
        content_type="application/octet-stream",
        upload_id="remote-id",
    )
    first.record_part(session["id"], 1, '"etag-one"')

    restored = UploadRegistry(path).get(session["id"])

    assert restored is not None
    assert restored["parts"]["1"] == '"etag-one"'

