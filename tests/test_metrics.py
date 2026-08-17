from r2_file_manager.metrics import parse_account_metrics


def test_parse_account_metrics_sums_storage_classes_and_states():
    result = parse_account_metrics(
        {
            "standard": {
                "published": {"payloadSize": 1000, "metadataSize": 10, "objects": 3},
                "uploaded": {"payloadSize": 200, "metadataSize": 2, "objects": 1},
            },
            "infrequentAccess": {
                "published": {"payloadSize": 500, "metadataSize": 5, "objects": 2},
                "uploaded": {"payloadSize": 100, "metadataSize": 1, "objects": 1},
            },
        }
    )

    assert result["stored_bytes"] == 1515
    assert result["payload_bytes"] == 1500
    assert result["metadata_bytes"] == 15
    assert result["objects"] == 5
    assert result["uploading_bytes"] == 303


def test_parse_account_metrics_accepts_missing_storage_classes():
    result = parse_account_metrics({})
    assert result["stored_bytes"] == 0
    assert result["objects"] == 0
