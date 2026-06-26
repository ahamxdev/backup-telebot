"""Tests for pipeline.py — compress, split, checksum, process_file."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from tgbot_backup.pipeline import (
    sha256_file,
    compress_gzip,
    split_file,
    process_file,
)


# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------


def test_sha256_file_known(tmp_path):
    import hashlib
    data = b"hello world"
    f = tmp_path / "test.bin"
    f.write_bytes(data)
    assert sha256_file(f) == hashlib.sha256(data).hexdigest()


def test_sha256_file_empty(tmp_path):
    import hashlib
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    assert sha256_file(f) == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# compress_gzip
# ---------------------------------------------------------------------------


def test_compress_gzip_produces_gz(tmp_path):
    src = tmp_path / "data.txt"
    src.write_bytes(b"hello" * 1000)
    dst = compress_gzip(src)
    assert dst.suffix == ".gz"
    assert dst.exists()
    assert not src.exists()  # removed by default


def test_compress_gzip_round_trip(tmp_path):
    original = b"This is the backup data" * 100
    src = tmp_path / "backup.sql"
    src.write_bytes(original)
    gz = compress_gzip(src, remove_src=False)
    with gzip.open(gz, "rb") as fh:
        recovered = fh.read()
    assert recovered == original


def test_compress_gzip_remove_false(tmp_path):
    src = tmp_path / "keep.txt"
    src.write_bytes(b"data")
    compress_gzip(src, remove_src=False)
    assert src.exists()


# ---------------------------------------------------------------------------
# split_file
# ---------------------------------------------------------------------------


def test_split_file_no_split_needed(tmp_path):
    f = tmp_path / "small.bin"
    f.write_bytes(b"x" * 100)
    parts = split_file(f, max_bytes=1000)
    assert parts == [f]


def test_split_file_two_parts(tmp_path):
    data = b"A" * 500 + b"B" * 500
    f = tmp_path / "big.bin"
    f.write_bytes(data)
    parts = split_file(f, max_bytes=500)
    assert len(parts) == 2
    assert parts[0].read_bytes() == b"A" * 500
    assert parts[1].read_bytes() == b"B" * 500


def test_split_file_reassembly(tmp_path):
    original = bytes(range(256)) * 40  # 10240 bytes
    f = tmp_path / "data.bin"
    f.write_bytes(original)
    parts = split_file(f, max_bytes=1000)
    reassembled = b"".join(p.read_bytes() for p in parts)
    assert reassembled == original


def test_split_file_naming(tmp_path):
    f = tmp_path / "backup.sql.gz"
    f.write_bytes(b"x" * 200)
    parts = split_file(f, max_bytes=100)
    names = [p.name for p in parts]
    assert "backup.sql.gz.part001" in names
    assert "backup.sql.gz.part002" in names


# ---------------------------------------------------------------------------
# process_file
# ---------------------------------------------------------------------------


def test_process_file_no_stages(tmp_path):
    data = b"raw backup data"
    src = tmp_path / "backup.sql"
    src.write_bytes(data)
    parts, checksum = process_file(src, split_size_mb=100)
    assert parts == [src]
    assert len(checksum) == 64  # hex sha256


def test_process_file_compress(tmp_path):
    src = tmp_path / "backup.sql"
    src.write_bytes(b"uncompressed data" * 100)
    parts, checksum = process_file(src, compress=True, split_size_mb=100)
    assert len(parts) == 1
    assert parts[0].name.endswith(".gz")
    assert len(checksum) == 64


def test_process_file_split(tmp_path):
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * 3 * 1024 * 1024)  # 3 MB
    parts, checksum = process_file(src, split_size_mb=1)
    assert len(parts) >= 2
    reassembled = b"".join(p.read_bytes() for p in parts)
    assert reassembled == b"x" * 3 * 1024 * 1024


def test_process_file_enforce_encryption_raises(tmp_path):
    src = tmp_path / "secret.sql"
    src.write_bytes(b"data")
    with pytest.raises(RuntimeError, match="enforce_encryption"):
        process_file(src, encrypt_recipient="", enforce_encryption=True)
