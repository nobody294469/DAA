"""Unit tests for Phase 8.3 Task 2: Encrypted Blob Storage.

Verifies:
  - Put and get roundtrip fidelity
  - Authenticated encryption at rest (raw on-disk ciphertext has no plaintext)
  - Nonce randomness and content-addressing
  - Storage reference determinism
  - Exists and deletion lifecycle
  - Decryption failure on wrong key or tampered ciphertext
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import pytest

from clindoc.config import ProductionProfileError
from clindoc.storage.blob import (
    BlobNotFoundError,
    DecryptionError,
    EncryptedFileBlobStore,
)


@pytest.fixture
def temp_blob_dir(tmp_path: Path) -> Path:
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir(parents=True, exist_ok=True)
    return blob_dir


@pytest.fixture
def blob_store(temp_blob_dir: Path) -> EncryptedFileBlobStore:
    # 32-byte key for AES-256
    key = b"0123456789abcdef0123456789abcdef"
    return EncryptedFileBlobStore(root_dir=temp_blob_dir, encryption_key=key)


def test_put_get_roundtrip(blob_store: EncryptedFileBlobStore):
    """Assert stored bytes are retrieved bit-for-bit identical."""
    payload = b"Sample Clinical Document PDF Header Content \x00\x01\x02"
    ref = blob_store.put(payload)

    assert ref.startswith("blob://local/")
    retrieved = blob_store.get(ref)
    assert retrieved == payload


def test_encrypted_at_rest_verification(blob_store: EncryptedFileBlobStore):
    """Assert raw bytes written to disk are strictly ciphertext (no plaintext leakage)."""
    sensitive_phi = b"CONFIDENTIAL PHI: Patient John Doe MRN 987654321 Diagnosed with T2DM"
    ref = blob_store.put(sensitive_phi)

    # Inspect raw file on disk directly
    raw_on_disk = blob_store.get_raw_ciphertext(ref)
    assert sensitive_phi not in raw_on_disk
    assert b"John Doe" not in raw_on_disk
    assert b"987654321" not in raw_on_disk
    assert b"T2DM" not in raw_on_disk

    # Decrypting through authorized interface restores exact plaintext
    decrypted = blob_store.get(ref)
    assert decrypted == sensitive_phi


def test_deterministic_storage_reference(blob_store: EncryptedFileBlobStore):
    """Assert identical document payloads yield identical deterministic storage references."""
    payload = b"Identical clinical report content across uploads"
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    ref1 = blob_store.put(payload)
    ref2 = blob_store.put(payload)

    assert ref1 == f"blob://local/{expected_sha256}"
    assert ref1 == ref2


def test_exists_and_deletion_lifecycle(blob_store: EncryptedFileBlobStore):
    """Assert existence checking, deletion, and subsequent 404 behavior."""
    payload = b"Temporary document to be purged under retention policy"
    ref = blob_store.put(payload)

    assert blob_store.exists(ref) is True
    deleted = blob_store.delete(ref)
    assert deleted is True
    assert blob_store.exists(ref) is False

    with pytest.raises(BlobNotFoundError):
        blob_store.get(ref)


def test_wrong_key_decryption_failure(temp_blob_dir: Path):
    """Assert ciphertext encrypted with Key A cannot be decrypted with Key B."""
    key_a = b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    key_b = b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    store_a = EncryptedFileBlobStore(root_dir=temp_blob_dir, encryption_key=key_a)
    store_b = EncryptedFileBlobStore(root_dir=temp_blob_dir, encryption_key=key_b)

    payload = b"Clinical notes for Key Isolation Test"
    ref = store_a.put(payload)

    # Retrieval with correct key succeeds
    assert store_a.get(ref) == payload

    # Retrieval with wrong key must fail closed with DecryptionError
    with pytest.raises(DecryptionError):
        store_b.get(ref)


def test_tampered_ciphertext_detection(blob_store: EncryptedFileBlobStore, temp_blob_dir: Path):
    """Assert any tampering with ciphertext bytes triggers AEAD authentication failure."""
    payload = b"Tamper detection test document payload"
    ref = blob_store.put(payload)

    sha256_hex = ref.removeprefix("blob://local/")
    disk_path = temp_blob_dir / sha256_hex[:2] / f"{sha256_hex}.enc"
    assert disk_path.exists()

    # Tamper with the raw ciphertext
    with open(disk_path, "r+b") as f:
        data = bytearray(f.read())
        # Flip a bit in the ciphertext / tag region
        data[-5] ^= 0xFF
        f.seek(0)
        f.write(data)

    with pytest.raises(DecryptionError):
        blob_store.get(ref)


def test_production_profile_missing_key_fails_closed(monkeypatch, temp_blob_dir: Path):
    """Assert production environment with missing encryption key fails closed (no fallback)."""
    monkeypatch.setenv("CLINDOC_PROFILE", "production")
    monkeypatch.delenv("CLINDOC_BLOB_ENCRYPTION_KEY", raising=False)

    with pytest.raises(ProductionProfileError, match="CLINDOC_BLOB_ENCRYPTION_KEY must be configured"):
        EncryptedFileBlobStore(root_dir=temp_blob_dir)


def test_production_profile_malformed_key_fails_closed(monkeypatch, temp_blob_dir: Path):
    """Assert production environment with malformed key fails closed with ValueError."""
    monkeypatch.setenv("CLINDOC_PROFILE", "production")
    monkeypatch.setenv("CLINDOC_BLOB_ENCRYPTION_KEY", "not-a-valid-256-bit-key")

    with pytest.raises(ValueError, match="Invalid CLINDOC_BLOB_ENCRYPTION_KEY"):
        EncryptedFileBlobStore(root_dir=temp_blob_dir)


def test_production_profile_valid_256bit_hex_key_succeeds(monkeypatch, temp_blob_dir: Path):
    """Assert production environment with valid 64-char hex key initializes and roundtrips cleanly."""
    valid_hex_key = "0123456789abcdef" * 4  # 64 hex chars = 32 bytes
    monkeypatch.setenv("CLINDOC_PROFILE", "production")
    monkeypatch.setenv("CLINDOC_BLOB_ENCRYPTION_KEY", valid_hex_key)

    store = EncryptedFileBlobStore(root_dir=temp_blob_dir)
    payload = b"Production clinical payload secured by AES-256-GCM"
    ref = store.put(payload)
    assert store.get(ref) == payload


def test_development_profile_missing_key_retains_dev_behavior(monkeypatch, temp_blob_dir: Path):
    """Assert development profile without key retains deterministic dev fallback."""
    monkeypatch.setenv("CLINDOC_PROFILE", "dev")
    monkeypatch.delenv("CLINDOC_BLOB_ENCRYPTION_KEY", raising=False)

    store = EncryptedFileBlobStore(root_dir=temp_blob_dir)
    payload = b"Dev environment test payload"
    ref = store.put(payload)
    assert store.get(ref) == payload


def test_key_validation_short_key_rejected(temp_blob_dir: Path):
    """Assert keys shorter than 32 bytes are rejected (no padding)."""
    with pytest.raises(ValueError, match="AES-256-GCM requires exactly 32 bytes"):
        EncryptedFileBlobStore(root_dir=temp_blob_dir, encryption_key=b"too_short_key")


def test_key_validation_long_key_rejected(temp_blob_dir: Path):
    """Assert keys longer than 32 bytes are rejected (no truncation)."""
    with pytest.raises(ValueError, match="AES-256-GCM requires exactly 32 bytes"):
        EncryptedFileBlobStore(root_dir=temp_blob_dir, encryption_key=b"a" * 33)


def test_env_key_invalid_hex_rejected(monkeypatch, temp_blob_dir: Path):
    """Assert 64-character environment key containing non-hex chars is rejected."""
    monkeypatch.setenv("CLINDOC_BLOB_ENCRYPTION_KEY", "z" * 64)
    with pytest.raises(ValueError, match="non-hex characters"):
        EncryptedFileBlobStore(root_dir=temp_blob_dir)


def test_env_key_valid_64_character_hex_accepted(monkeypatch, temp_blob_dir: Path):
    """Assert 64-character hex environment key is accepted and decoded to 32 bytes."""
    valid_hex = "f" * 64
    monkeypatch.setenv("CLINDOC_BLOB_ENCRYPTION_KEY", valid_hex)
    store = EncryptedFileBlobStore(root_dir=temp_blob_dir)
    assert len(store._key) == 32
    assert store._key == bytes.fromhex(valid_hex)

