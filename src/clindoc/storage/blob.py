"""Encrypted blob storage service for raw document binaries and page artifacts.

Authority:
  - PRODUCTIZATION_SPEC.md §4.2, §4.4, §5 (Threat T-04, T-18)
  - PRODUCTIZATION_DECISIONS.md PD-007, §7 Item 3

Implements local authenticated AES-256-GCM encryption-at-rest for document binaries,
ensuring raw PHI is never stored as plaintext on disk and the database only stores
cryptographic digests and storage reference URIs.
"""

from __future__ import annotations

import abc
import hashlib
import os
from pathlib import Path
import re
from typing import Optional
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from clindoc.config import ProductionProfileError, RuntimeProfile, get_active_profile

HEX_64_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class BlobStorageError(Exception):
    """Base exception for all blob storage operations."""
    pass


class BlobNotFoundError(BlobStorageError):
    """Raised when a requested storage_ref does not exist in the blob store."""
    pass


class DecryptionError(BlobStorageError):
    """Raised when ciphertext cannot be authenticated or decrypted (wrong key or tampering)."""
    pass


class BlobStore(abc.ABC):
    """Abstract base class for document binary blob storage."""

    @abc.abstractmethod
    def put(self, data: bytes, document_id: Optional[UUID] = None) -> str:
        """Store bytes securely and return a canonical storage reference URI."""
        raise NotImplementedError

    @abc.abstractmethod
    def get(self, storage_ref: str) -> bytes:
        """Retrieve and decrypt bytes referenced by storage_ref."""
        raise NotImplementedError

    @abc.abstractmethod
    def exists(self, storage_ref: str) -> bool:
        """Return True if storage_ref exists in storage."""
        raise NotImplementedError

    @abc.abstractmethod
    def delete(self, storage_ref: str) -> bool:
        """Delete blob referenced by storage_ref. Return True if deleted, False if not found."""
        raise NotImplementedError


class EncryptedFileBlobStore(BlobStore):
    """Local filesystem blob store implementing authenticated AES-256-GCM encryption-at-rest.

    Each blob is encrypted using a 256-bit key and a cryptographically random 96-bit (12-byte) nonce.
    Stored file format on disk:
        [12 bytes nonce] + [ciphertext with 16 bytes GCM authentication tag]
    
    Content-addressed storage scheme:
        Canonical storage_ref URI: 'blob://local/{sha256}'
        Disk layout: {root_dir}/{sha256[:2]}/{sha256}.enc
    """

    NONCE_SIZE_BYTES = 12
    KEY_SIZE_BYTES = 32  # 256-bit key for AES-256

    def __init__(self, root_dir: Path | str, encryption_key: Optional[bytes] = None) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        
        active_profile = get_active_profile()

        # Resolve 256-bit encryption key
        if encryption_key is not None:
            if not isinstance(encryption_key, (bytes, bytearray)):
                raise TypeError(f"encryption_key must be bytes, got {type(encryption_key).__name__}.")
            if len(encryption_key) != self.KEY_SIZE_BYTES:
                raise ValueError(
                    f"Invalid key length: AES-256-GCM requires exactly 32 bytes (256 bits), got {len(encryption_key)} bytes."
                )
            self._key = bytes(encryption_key)
        else:
            env_key = os.environ.get("CLINDOC_BLOB_ENCRYPTION_KEY")
            if env_key is not None and env_key.strip():
                clean_key = env_key.strip()
                # Accept:
                # 1) exactly 64 hexadecimal characters -> decoded to 32 bytes
                # 2) exact 32-byte representation
                if len(clean_key) == 64:
                    try:
                        self._key = bytes.fromhex(clean_key)
                    except ValueError as exc:
                        raise ValueError(
                            f"Invalid CLINDOC_BLOB_ENCRYPTION_KEY: 64-character string contains non-hex characters: {exc}."
                        ) from exc
                elif len(clean_key.encode("utf-8")) == self.KEY_SIZE_BYTES:
                    self._key = clean_key.encode("utf-8")
                else:
                    raise ValueError(
                        f"Invalid CLINDOC_BLOB_ENCRYPTION_KEY: expected exactly 64 hex characters (32 bytes) or 32 raw bytes, "
                        f"got {len(clean_key)} characters ({len(clean_key.encode('utf-8'))} bytes). Truncation or padding is strictly forbidden."
                    )
            else:
                # Key is missing from environment
                if active_profile == RuntimeProfile.PRODUCTION:
                    raise ProductionProfileError(
                        "Production security invariant violation: CLINDOC_BLOB_ENCRYPTION_KEY must be configured in PRODUCTION profile. "
                        "Fallback development encryption keys are strictly forbidden for production data (PD-019, Threat T-04)."
                    )
                # Development-only fallback: deterministic derived key for test determinism
                self._key = hashlib.sha256(b"clindoc_dev_blob_encryption_key_v1").digest()

        if len(self._key) != self.KEY_SIZE_BYTES:
            raise ValueError(
                f"AES-256-GCM requires a 32-byte key (got {len(self._key)} bytes)."
            )
        self._aesgcm = AESGCM(self._key)

    def _parse_storage_ref(self, storage_ref: str) -> str:
        """Extract SHA-256 hex digest from storage_ref URI and validate format."""
        if not isinstance(storage_ref, str):
            raise BlobStorageError(f"Invalid storage reference type: {type(storage_ref).__name__}.")
        cleaned = storage_ref.strip()
        if cleaned.startswith("blob://local/"):
            sha256_hex = cleaned[len("blob://local/"):]
        elif cleaned.startswith("file://"):
            p = Path(cleaned[len("file://"):])
            sha256_hex = p.name.removesuffix(".enc")
        else:
            sha256_hex = cleaned

        if not HEX_64_PATTERN.match(sha256_hex):
            raise BlobStorageError(
                f"Invalid or unsafe storage reference '{storage_ref}': "
                "expected 64-character hexadecimal SHA-256 digest."
            )
        return sha256_hex.lower()

    def _get_path_for_sha256(self, sha256_hex: str) -> Path:
        """Compute hierarchical file path for content hash and verify root containment."""
        if not HEX_64_PATTERN.match(sha256_hex):
            raise BlobStorageError(
                f"Invalid or unsafe SHA-256 digest: '{sha256_hex}'. "
                "Must be a 64-character hexadecimal string."
            )
        prefix = sha256_hex[:2].lower()
        target_path = (self.root_dir / prefix / f"{sha256_hex.lower()}.enc").resolve()
        try:
            target_path.relative_to(self.root_dir)
        except ValueError:
            raise BlobStorageError("Path traversal detected: target path is outside blob root directory.")
        return target_path

    def put(self, data: bytes, document_id: Optional[UUID] = None) -> str:
        """Encrypt and persist raw document bytes using AES-256-GCM.

        Returns deterministic storage_ref URI 'blob://local/{sha256}'.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("Data to store must be bytes.")

        sha256_hex = hashlib.sha256(data).hexdigest()
        target_path = self._get_path_for_sha256(sha256_hex)
        storage_ref = f"blob://local/{sha256_hex}"

        # If identical ciphertext file already exists on disk, reuse it (idempotent / deduplicated)
        if target_path.exists():
            return storage_ref

        target_path.parent.mkdir(parents=True, exist_ok=True)

        nonce = os.urandom(self.NONCE_SIZE_BYTES)
        # Associated data binds the content SHA-256 to prevent ciphertext relocation
        associated_data = sha256_hex.encode("ascii")
        ciphertext = self._aesgcm.encrypt(nonce, data, associated_data)

        # Atomic write via temporary file
        tmp_path = target_path.with_suffix(".tmp")
        with open(tmp_path, "wb") as f:
            f.write(nonce)
            f.write(ciphertext)
        tmp_path.replace(target_path)

        return storage_ref

    def get(self, storage_ref: str) -> bytes:
        """Retrieve and decrypt payload from encrypted storage. Raises DecryptionError on tamper/wrong key."""
        sha256_hex = self._parse_storage_ref(storage_ref)
        target_path = self._get_path_for_sha256(sha256_hex)

        if not target_path.exists():
            raise BlobNotFoundError(f"Blob for {storage_ref} not found on disk.")

        with open(target_path, "rb") as f:
            raw_payload = f.read()

        if len(raw_payload) < self.NONCE_SIZE_BYTES + 16:  # 12-byte nonce + 16-byte tag
            raise DecryptionError(f"Corrupted blob payload for {storage_ref}: file too short.")

        nonce = raw_payload[:self.NONCE_SIZE_BYTES]
        ciphertext = raw_payload[self.NONCE_SIZE_BYTES:]
        associated_data = sha256_hex.encode("ascii")

        try:
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, associated_data)
        except InvalidTag as exc:
            raise DecryptionError(
                f"Failed to authenticate/decrypt blob {storage_ref}. Key mismatch or data tampered."
            ) from exc

        # Integrity verification
        if hashlib.sha256(plaintext).hexdigest() != sha256_hex:
            raise DecryptionError(
                f"Plaintext SHA-256 digest mismatch for {storage_ref}."
            )

        return plaintext

    def get_raw_ciphertext(self, storage_ref: str) -> bytes:
        """Retrieve raw on-disk ciphertext without decryption (for security auditing & testing)."""
        sha256_hex = self._parse_storage_ref(storage_ref)
        target_path = self._get_path_for_sha256(sha256_hex)
        if not target_path.exists():
            raise BlobNotFoundError(f"Blob for {storage_ref} not found on disk.")
        with open(target_path, "rb") as f:
            return f.read()

    def exists(self, storage_ref: str) -> bool:
        """Check if blob file exists."""
        sha256_hex = self._parse_storage_ref(storage_ref)
        return self._get_path_for_sha256(sha256_hex).exists()

    def delete(self, storage_ref: str) -> bool:
        """Securely remove blob file from disk."""
        sha256_hex = self._parse_storage_ref(storage_ref)
        target_path = self._get_path_for_sha256(sha256_hex)
        if target_path.exists():
            target_path.unlink()
            return True
        return False
