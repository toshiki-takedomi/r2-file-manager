import math

import pytest

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
