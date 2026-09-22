import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.finalize import delivery_artifacts, finalize_artifact, recover_finalized


@pytest.mark.parametrize("size", [9, 10])
def test_at_or_below_limit_preserves_single_file(tmp_path: Path, size: int) -> None:
    payload = bytes(range(size))
    source = tmp_path / "source.tar.zst"
    source.write_bytes(payload)

    finalized = finalize_artifact(
        source,
        tmp_path / "pending",
        "profile",
        "00000000-0000-4000-8000-000000000001",
        datetime(2026, 9, 22, tzinfo=UTC),
        "UTC",
        "backup.tar.zst",
        {},
        max_file_bytes=10,
    )

    assert finalized.parts == ()
    assert finalized.artifact.read_bytes() == payload
    assert [
        item.path
        for item in delivery_artifacts(
            finalized.directory, finalized.artifact.name, size, finalized.checksum
        )
    ] == [finalized.artifact]


@pytest.mark.parametrize(
    ("size", "expected_sizes"),
    [(11, [10, 1]), (25, [10, 10, 5]), (40, [10, 10, 10, 10])],
)
def test_oversized_final_artifact_is_split_and_reassembles_exactly(
    tmp_path: Path, size: int, expected_sizes: list[int]
) -> None:
    payload = bytes(index % 251 for index in range(size))
    source = tmp_path / "source.tar.zst"
    source.write_bytes(payload)
    original_sha256 = hashlib.sha256(payload).hexdigest()

    finalized = finalize_artifact(
        source,
        tmp_path / "pending",
        "profile",
        "00000000-0000-4000-8000-000000000001",
        datetime(2026, 9, 22, tzinfo=UTC),
        "UTC",
        "backup.tar.zst",
        {},
        max_file_bytes=10,
    )

    assert not finalized.artifact.exists()
    assert [part.path.name for part in finalized.parts] == [
        f"backup.tar.zst.part{number:03d}" for number in range(1, len(expected_sizes) + 1)
    ]
    assert [part.size for part in finalized.parts] == expected_sizes
    reassembled = b"".join(part.path.read_bytes() for part in finalized.parts)
    assert reassembled == payload
    assert hashlib.sha256(reassembled).hexdigest() == original_sha256 == finalized.checksum

    manifest = json.loads(finalized.manifest.read_text())
    assert manifest["sha256"] == original_sha256
    assert [part["sha256"] for part in manifest["parts"]] == [
        hashlib.sha256(part.path.read_bytes()).hexdigest() for part in finalized.parts
    ]
    recovered = recover_finalized(
        tmp_path / "pending", finalized.directory, "00000000-0000-4000-8000-000000000001"
    )
    assert recovered is not None
    assert recovered[0].parts == finalized.parts
