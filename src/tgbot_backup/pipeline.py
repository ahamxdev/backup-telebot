"""File processing pipeline: compress → encrypt → checksum → split.

All stages are optional and are enabled per-job or globally via config.
process_file() returns (list_of_parts, sha256_hex) where the parts are
the final files to upload (one per Telegram message).

Split uses pure Python so no external tools are needed.
Compression uses the built-in gzip module.
Encryption uses the system 'age' or 'gpg' binary.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_PART_SUFFIX = ".part{:03d}"


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of *path*, reading in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Compress
# ---------------------------------------------------------------------------


def compress_gzip(src: Path, *, remove_src: bool = True) -> Path:
    """Gzip-compress *src*, returning the .gz path.

    The original file is deleted if *remove_src* is True (default).
    """
    dst = src.parent / (src.name + ".gz")
    with open(src, "rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out)
    if remove_src:
        src.unlink()
    logger.info("Compressed %s → %s", src.name, dst.name)
    return dst


# ---------------------------------------------------------------------------
# Encrypt
# ---------------------------------------------------------------------------


def encrypt_age(src: Path, recipient: str, *, remove_src: bool = True) -> Path:
    """Encrypt *src* with `age --recipient <recipient>`, returning the .age path."""
    if not shutil.which("age"):
        raise RuntimeError(
            "'age' is not installed. "
            "See https://github.com/FiloSottile/age for installation instructions."
        )
    dst = src.parent / (src.name + ".age")
    result = subprocess.run(
        ["age", "--encrypt", "--recipient", recipient, "--output", str(dst), str(src)],
        capture_output=True,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"age encryption failed: {stderr}")
    if remove_src:
        src.unlink()
    logger.info("Encrypted %s → %s (age)", src.name, dst.name)
    return dst


def encrypt_gpg(src: Path, recipient: str, *, remove_src: bool = True) -> Path:
    """Encrypt *src* with `gpg --encrypt --recipient <recipient>`, returning the .gpg path."""
    if not shutil.which("gpg"):
        raise RuntimeError("'gpg' is not installed.")
    dst = src.parent / (src.name + ".gpg")
    result = subprocess.run(
        [
            "gpg", "--batch", "--yes",
            "--encrypt", "--recipient", recipient,
            "--output", str(dst), str(src),
        ],
        capture_output=True,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"GPG encryption failed: {stderr}")
    if remove_src:
        src.unlink()
    logger.info("Encrypted %s → %s (GPG)", src.name, dst.name)
    return dst


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------


def split_file(src: Path, max_bytes: int) -> list[Path]:
    """Split *src* into ≤ *max_bytes* chunks.

    Returns a list containing just [src] if the file fits in one part.
    Parts are named <original>.part001, .part002, …
    The original file is NOT deleted by this function.
    """
    if src.stat().st_size <= max_bytes:
        return [src]

    parts: list[Path] = []
    with open(src, "rb") as fh:
        part_num = 1
        while True:
            chunk = fh.read(max_bytes)
            if not chunk:
                break
            part_path = src.parent / (src.name + _PART_SUFFIX.format(part_num))
            part_path.write_bytes(chunk)
            parts.append(part_path)
            part_num += 1

    logger.info(
        "Split %s into %d part(s) (≤ %.1f MB each).",
        src.name, len(parts), max_bytes / (1024 * 1024),
    )
    return parts


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------


def process_file(
    src: Path,
    *,
    compress: bool = False,
    encrypt_recipient: str = "",
    encrypt_tool: str = "age",
    enforce_encryption: bool = False,
    split_size_mb: int = 45,
) -> tuple[list[Path], str]:
    """Run the full pipeline on *src* and return (parts, sha256).

    Stages:
      1. Compress  (if compress=True)
      2. Encrypt   (if encrypt_recipient is non-empty)
      3. Checksum  (sha256 of the post-compress/encrypt file)
      4. Split     (if the file exceeds split_size_mb * 1024**2 bytes)

    If enforce_encryption=True and no encrypt_recipient is set, raises RuntimeError.
    Intermediate files are cleaned up automatically.
    The caller is responsible for cleaning up the returned parts after upload.
    """
    if enforce_encryption and not encrypt_recipient:
        raise RuntimeError(
            "enforce_encryption is True but no encrypt_recipient is configured. "
            "Refusing to upload an unencrypted backup."
        )

    current = src
    intermediates: list[Path] = []

    # Stage 1: Compress
    if compress and not current.name.endswith((".gz", ".zst", ".xz", ".bz2", ".age", ".gpg")):
        compressed = compress_gzip(current, remove_src=(current != src))
        if current != src:
            intermediates.append(current)
        current = compressed
        intermediates.append(current)

    # Stage 2: Encrypt
    if encrypt_recipient:
        if encrypt_tool.lower() == "gpg":
            encrypted = encrypt_gpg(current, encrypt_recipient, remove_src=(current != src))
        else:
            encrypted = encrypt_age(current, encrypt_recipient, remove_src=(current != src))
        if current not in intermediates and current != src:
            intermediates.append(current)
        current = encrypted
        intermediates.append(current)

    # Stage 3: Checksum (on the final single-file form)
    checksum = sha256_file(current)
    logger.info("SHA-256(%s) = %s", current.name, checksum)

    # Stage 4: Split
    max_bytes = split_size_mb * 1024 * 1024
    parts = split_file(current, max_bytes)

    if len(parts) > 1:
        # The pre-split file is an intermediate; parts are the final outputs
        if current != src and current not in intermediates:
            intermediates.append(current)
        # Remove current (pre-split) after splitting
        if current.exists():
            try:
                current.unlink()
            except OSError as exc:
                logger.warning("Could not remove pre-split file %s: %s", current, exc)

    # Clean up any leftover intermediates that aren't the final outputs
    final_set = set(parts)
    for p in intermediates:
        if p not in final_set and p != src and p.exists():
            try:
                p.unlink()
            except OSError as exc:
                logger.warning("Could not remove intermediate %s: %s", p, exc)

    return parts, checksum
