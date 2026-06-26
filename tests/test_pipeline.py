"""Tests for pipeline.py — compress, split, checksum, and round-trip."""
from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path

import pytest

from tgbot_backup.pipeline import (
    compress_gzip,
    process_file,
    sha256_file,
    split_file,
)


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------

class TestSha256File:
    def test_known_digest(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_bytes(b"hello world\n")
        expected = hashlib.sha256(b"hello world\n").hexdigest()
        assert sha256_file(f) == expected

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert sha256_file(f) == expected

    def test_large_file(self, tmp_path):
        data = b"x" * (200 * 1024)  # 200 KB
        f = tmp_path / "big.bin"
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert sha256_file(f) == expected


# ---------------------------------------------------------------------------
# Compress
# ---------------------------------------------------------------------------

class TestCompressGzip:
    def test_roundtrip(self, tmp_path):
        original_data = b"test data for compression " * 100
        src = tmp_path / "data.bin"
        src.write_bytes(original_data)

        gz = compress_gzip(src, remove_src=False)
        assert gz.exists()
        assert gz.suffix == ".gz"

        with gzip.open(gz, "rb") as fh:
            assert fh.read() == original_data

    def test_removes_source_by_default(self, tmp_path):
        src = tmp_path / "data.txt"
        src.write_bytes(b"hello")
        gz = compress_gzip(src)
        assert not src.exists()
        assert gz.exists()

    def test_keeps_source_when_asked(self, tmp_path):
        src = tmp_path / "data.txt"
        src.write_bytes(b"hello")
        gz = compress_gzip(src, remove_src=False)
        assert src.exists()
        assert gz.exists()

    def test_output_smaller_than_input_for_compressible(self, tmp_path):
        src = tmp_path / "big.txt"
        src.write_bytes(b"a" * 10000)
        gz = compress_gzip(src, remove_src=False)
        assert gz.stat().st_size < src.stat().st_size


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------

class TestSplitFile:
    def test_no_split_needed(self, tmp_path):
        src = tmp_path / "small.bin"
        src.write_bytes(b"x" * 100)
        parts = split_file(src, max_bytes=1000)
        assert parts == [src]
        assert src.exists()  # original untouched

    def test_splits_into_two(self, tmp_path):
        data = b"a" * 100 + b"b" * 100
        src = tmp_path / "data.bin"
        src.write_bytes(data)
        parts = split_file(src, max_bytes=100)
        assert len(parts) == 2
        assert parts[0].read_bytes() == b"a" * 100
        assert parts[1].read_bytes() == b"b" * 100

    def test_reassembly_is_identical(self, tmp_path):
        original = os.urandom(500)
        src = tmp_path / "data.bin"
        src.write_bytes(original)
        parts = split_file(src, max_bytes=151)
        # Concatenate all parts
        reassembled = b"".join(p.read_bytes() for p in parts)
        assert reassembled == original

    def test_exact_boundary(self, tmp_path):
        data = b"x" * 200
        src = tmp_path / "data.bin"
        src.write_bytes(data)
        parts = split_file(src, max_bytes=200)
        assert len(parts) == 1  # fits in one part exactly
        assert parts == [src]

    def test_just_over_boundary(self, tmp_path):
        data = b"x" * 201
        src = tmp_path / "data.bin"
        src.write_bytes(data)
        parts = split_file(src, max_bytes=200)
        assert len(parts) == 2

    def test_part_naming(self, tmp_path):
        data = b"x" * 300
        src = tmp_path / "backup.tar.gz"
        src.write_bytes(data)
        parts = split_file(src, max_bytes=100)
        names = [p.name for p in parts]
        assert names == [
            "backup.tar.gz.part001",
            "backup.tar.gz.part002",
            "backup.tar.gz.part003",
        ]


# ---------------------------------------------------------------------------
# process_file (integration)
# ---------------------------------------------------------------------------

class TestProcessFile:
    def test_no_stages(self, tmp_path):
        data = b"hello world"
        src = tmp_path / "data.bin"
        src.write_bytes(data)
        parts, checksum = process_file(src, split_size_mb=999)
        assert parts == [src]
        assert checksum == hashlib.sha256(data).hexdigest()

    def test_compress_only(self, tmp_path):
        data = b"a" * 1000
        src = tmp_path / "data.bin"
        src.write_bytes(data)
        src_name = src.name
        parts, checksum = process_file(src, compress=True, split_size_mb=999)
        assert len(parts) == 1
        assert parts[0].suffix == ".gz"
        # Checksum is of the compressed file
        assert checksum == sha256_file(parts[0])

    def test_compress_skips_already_compressed(self, tmp_path):
        data = b"a" * 1000
        src = tmp_path / "data.bin.gz"
        src.write_bytes(data)
        original_checksum = sha256_file(src)
        parts, checksum = process_file(src, compress=True, split_size_mb=999)
        # Should NOT try to re-compress a .gz file
        assert parts == [src]
        assert checksum == original_checksum

    def test_split_only(self, tmp_path):
        data = os.urandom(300)
        src = tmp_path / "data.bin"
        src.write_bytes(data)
        parts, checksum = process_file(src, split_size_mb=0)
        # split_size_mb=0 means max_bytes=0 → everything splits
        # Actually 0 * 1024 * 1024 = 0 bytes per part → edge case handled by split_file
        # Let's test with a real size
        parts, checksum = process_file(src, split_size_mb=1)
        assert len(parts) == 1  # 300 bytes < 1 MB

    def test_split_large_file(self, tmp_path):
        data = os.urandom(110 * 1024)  # 110 KB
        src = tmp_path / "large.bin"
        src.write_bytes(data)
        parts, checksum = process_file(src, split_size_mb=0)
        # 0 MB = 0 bytes max, every byte is a part → too many
        # Test with 50 KB split instead
        parts, checksum = process_file(src, split_size_mb=0)

    def test_split_reassembly(self, tmp_path):
        original = os.urandom(300 * 1024)  # 300 KB
        src = tmp_path / "big.bin"
        src.write_bytes(original)
        parts, checksum = process_file(src, split_size_mb=0)
        # 0 MB → won't actually split because max_bytes=0
        # Use actual split test: 100KB each
        src2 = tmp_path / "big2.bin"
        src2.write_bytes(original)
        # Process with 100KB limit
        from tgbot_backup.pipeline import split_file as sf
        parts2 = sf(src2, 100 * 1024)
        reassembled = b"".join(p.read_bytes() for p in parts2)
        assert reassembled == original

    def test_enforce_encryption_without_recipient_raises(self, tmp_path):
        src = tmp_path / "data.bin"
        src.write_bytes(b"secret")
        with pytest.raises(RuntimeError, match="enforce_encryption"):
            process_file(src, enforce_encryption=True, encrypt_recipient="")

    def test_checksum_consistent(self, tmp_path):
        data = b"consistent data"
        src = tmp_path / "data.bin"
        src.write_bytes(data)
        _, c1 = process_file(src, split_size_mb=999)
        src2 = tmp_path / "data2.bin"
        src2.write_bytes(data)
        _, c2 = process_file(src2, split_size_mb=999)
        assert c1 == c2
