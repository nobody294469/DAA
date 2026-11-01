"""Forensic Quarantine and Platform-Safe Truncation Protocol for Clinical Audit Ledgers.

Conforms strictly to CLINICALDOC_STEP3_DEF003_IMPLEMENTATION_EXECUTION_PLAN.md (Revision 2.7 §5).

Guarantees:
1. Cooperative Concurrency Protection: Invoked strictly under `<ledger>.lock`.
2. Windows Mandatory Write-Lockout: Opens source handle via Win32 `_winapi.CreateFile`
   with `FILE_SHARE_READ`, kernel-denying `FILE_SHARE_WRITE` throughout the operation.
3. Single Source Descriptor: Held open continuously across initial verification, stream-copy,
   post-copy re-read, and in-place truncation.
4. Independent Destination Disk Verification: Destination is closed, fsynced, reopened,
   and independently re-hashed from physical disk.
5. Post-Copy Source Integrity: Source is rewound and re-hashed; any concurrent append or
   inode/device change aborts without modifying original.
6. POSIX Fail-Closed Invariant: Automatic truncation is strictly prohibited on POSIX.
   Unquiesced concurrent modifications mark quarantine as `.untrusted` and fail closed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

from clindoc.audit.locking import flush_and_fsync, fsync_directory, locked_audit_ledger

logger = logging.getLogger(__name__)


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────

class ForensicQuarantineError(Exception):
    """Base exception for forensic quarantine and truncation failures."""


class ForensicQuarantineVerificationError(ForensicQuarantineError):
    """Raised when independent destination disk verification does not match source stream."""


class ExternalConcurrentModificationError(ForensicQuarantineError):
    """Raised when source file was modified concurrently during quarantine copy."""


class UnquiescedLedgerModificationError(ForensicQuarantineError):
    """Raised when non-cooperative writers appended to POSIX ledger or truncation is attempted on POSIX."""


class UnsupportedFilesystemError(ForensicQuarantineError):
    """Raised when filesystem does not support unique file identifiers (e.g. 64-bit NTFS file index)."""


# ─── TORN-TAIL OFFSET DETECTOR ───────────────────────────────────────────────

def find_valid_tail_offset(
    ledger_path: Union[str, Path],
) -> Tuple[Optional[int], int, str]:
    """Scan the ledger file to detect torn tails vs middle corruption.

    Returns:
        Tuple of (valid_tail_offset, last_valid_sequence, last_valid_hash).
        - If trailing bytes are a torn write at EOF after a valid prefix (sequences 1..N),
          `valid_tail_offset` is the byte offset of the end of the last complete valid line.
        - If corruption occurs in the middle (subsequent records exist in the file),
          or if sequence numbers are invalid/missing, `valid_tail_offset` is None.
    """
    l_path = Path(ledger_path)
    if not l_path.exists() or l_path.stat().st_size == 0:
        return None, 0, ""

    last_good_offset = 0
    last_seq = 0
    last_hash = ""
    expected_seq = 1
    has_corruption = False

    with open(l_path, "rb") as f:
        while True:
            raw_line = f.readline()
            if not raw_line:
                break

            line_end_offset = f.tell()
            line_str = raw_line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                record = json.loads(line_str)
                seq = record.get("sequence_number")
                curr_h = record.get("current_event_hash")

                if not isinstance(seq, int) or seq != expected_seq or not curr_h:
                    has_corruption = True
                    break

                last_good_offset = line_end_offset
                last_seq = seq
                last_hash = str(curr_h).strip()
                expected_seq += 1
            except Exception:
                has_corruption = True
                break

        if has_corruption:
            # If no valid records existed before corruption, cannot truncate
            if last_seq == 0 or last_good_offset == 0:
                return None, 0, ""

            # Check if any subsequent non-empty lines exist in the file
            # If so, this is strictly a MIDDLE corruption; automatic truncation is prohibited!
            while True:
                nxt = f.readline()
                if not nxt:
                    break
                if nxt.strip():
                    return None, last_seq, last_hash

            # Only a torn write at EOF has no subsequent lines
            return last_good_offset, last_seq, last_hash

    return None, last_seq, last_hash


# ─── VERIFIED FORENSIC QUARANTINE ────────────────────────────────────────────

def execute_verified_forensic_quarantine(
    ledger_path: Union[str, Path],
    valid_tail_offset: Optional[int] = None,
    chunk_size: int = 65536,
    _copy_chunk_hook: Optional[Callable[[Path, int], None]] = None,
    _post_verify_hook: Optional[Callable[[Path], None]] = None,
    _force_posix_mode: bool = False,
) -> Path:
    """Execute verified forensic quarantine with independent disk verification and platform-safe truncation.

    Adheres strictly to Revision 2.7 §5.1:
      1. Open source handle in binary read/write mode ("r+b").
         On Windows: _winapi.CreateFile with FILE_SHARE_READ (denying external write sharing).
         On POSIX: open("r+b") under cooperative lock.
      2. Record initial stat identity (dev, ino, size, mtime).
      3. Stream-copy to temporary destination `<quarantine>.tmp.<pid>` computing SHA-256.
      4. Flush and fsync temporary destination, then close.
      5. Reopen destination from disk and independently recompute byte count and SHA-256.
      6. Rewind open source handle and perform post-copy source re-read and SHA-256 verification.
      7. Finalize quarantine copy via atomic rename (os.replace) and directory fsync.
      8. On POSIX: check post-replace stat for unquiesced append; fail closed without truncation.
      9. On Windows: if valid_tail_offset is specified, truncate open source handle under kernel lockout.
    """
    l_path = Path(ledger_path)
    if not l_path.exists():
        raise FileNotFoundError(f"Cannot quarantine non-existent ledger: {l_path}")

    iso_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_path = l_path.with_name(f"{l_path.name}.corrupt.{iso_ts}")
    tmp_quarantine_path = quarantine_path.with_name(f"{quarantine_path.name}.tmp.{os.getpid()}")

    src_copy_hasher = hashlib.sha256()
    bytes_copied = 0
    is_nt = (os.name == "nt") and (not _force_posix_mode)

    try:
        # 1. Open source handle in binary read/write mode ("r+b")
        if is_nt:
            import msvcrt
            import _winapi

            # GENERIC_READ (0x80000000) | GENERIC_WRITE (0x40000000)
            # FILE_SHARE_READ (0x00000001) denies FILE_SHARE_WRITE and FILE_SHARE_DELETE
            win_handle = _winapi.CreateFile(
                str(l_path.resolve()),
                0x80000000 | 0x40000000,
                0x00000001,  # FILE_SHARE_READ
                0,
                3,  # OPEN_EXISTING
                0x80,  # FILE_ATTRIBUTE_NORMAL
                0,
            )
            fd = msvcrt.open_osfhandle(win_handle, os.O_RDWR | os.O_BINARY)
            f_src = open(fd, "r+b", closefd=True)
        else:
            f_src = open(l_path, "r+b")

        with f_src:
            stat_init = os.fstat(f_src.fileno())
            if is_nt and stat_init.st_ino == 0:
                raise UnsupportedFilesystemError("Filesystem does not support unique 64-bit file identifiers.")

            # 2. Stream-copy to temporary destination
            tmp_quarantine_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_quarantine_path, "wb") as f_dst:
                chunk_idx = 0
                while True:
                    chunk = f_src.read(chunk_size)
                    if not chunk:
                        break
                    if _copy_chunk_hook:
                        _copy_chunk_hook(l_path, chunk_idx)
                    src_copy_hasher.update(chunk)
                    f_dst.write(chunk)
                    bytes_copied += len(chunk)
                    chunk_idx += 1

                f_dst.flush()
                os.fsync(f_dst.fileno())

            # 3. Independently reopen destination from disk and compute byte count & SHA-256
            dst_hasher = hashlib.sha256()
            dst_bytes = 0
            with open(tmp_quarantine_path, "rb") as f_dst_verify:
                while True:
                    chunk = f_dst_verify.read(chunk_size)
                    if not chunk:
                        break
                    dst_hasher.update(chunk)
                    dst_bytes += len(chunk)

            if dst_hasher.hexdigest() != src_copy_hasher.hexdigest() or dst_bytes != bytes_copied:
                raise ForensicQuarantineVerificationError(
                    f"Independent destination verification failed: copied={bytes_copied}, verified={dst_bytes}"
                )

            # 4. Post-copy source re-read: rewind open source handle and verify complete bytes
            f_src.seek(0)
            src_post_hasher = hashlib.sha256()
            src_post_bytes = 0
            while True:
                chunk = f_src.read(chunk_size)
                if not chunk:
                    break
                src_post_hasher.update(chunk)
                src_post_bytes += len(chunk)

            stat_final = os.fstat(f_src.fileno())
            path_stat_final = l_path.stat()

            # Verify source content and identity remained identical throughout copy
            if (
                src_post_hasher.hexdigest() != src_copy_hasher.hexdigest()
                or src_post_bytes != bytes_copied
                or stat_final.st_mtime_ns != stat_init.st_mtime_ns
                or stat_final.st_dev != stat_init.st_dev
                or stat_final.st_ino != stat_init.st_ino
                or path_stat_final.st_ino != stat_init.st_ino
            ):
                raise ExternalConcurrentModificationError(
                    f"Source file {l_path} was modified concurrently outside cooperative lock during quarantine."
                )

            # Optional hook before atomic replace
            if _post_verify_hook:
                _post_verify_hook(l_path)

            # 5. Finalize quarantine copy via atomic rename before modifying original
            os.replace(tmp_quarantine_path, quarantine_path)
            if not is_nt:
                fsync_directory(l_path.parent)

                # POSIX Quarantine Finalization Boundary Check:
                # Check whether non-cooperative writer appended after source re-read but before/during replace
                stat_post_replace = os.fstat(f_src.fileno())
                if (
                    stat_post_replace.st_size != stat_init.st_size
                    or stat_post_replace.st_mtime_ns != stat_init.st_mtime_ns
                ):
                    untrusted_quarantine = quarantine_path.with_name(f"{quarantine_path.name}.untrusted")
                    os.replace(quarantine_path, untrusted_quarantine)
                    raise UnquiescedLedgerModificationError(
                        f"Non-cooperative writer appended to {l_path} during quarantine finalization. "
                        f"Quarantine marked untrusted ({untrusted_quarantine}). "
                        f"Original file preserved AS-IS. Process quiescence required for recovery."
                    )

            # 6. Platform-Specific Truncation Policy
            if valid_tail_offset is not None:
                if is_nt:
                    # Windows: Kernel denies FILE_SHARE_WRITE; safe to truncate under lock
                    f_src.seek(valid_tail_offset)
                    f_src.truncate(valid_tail_offset)
                    flush_and_fsync(f_src)
                else:
                    # POSIX: Fail closed. Automatic truncation strictly prohibited without kernel write-exclusion.
                    raise UnquiescedLedgerModificationError(
                        f"Automatic truncation on POSIX is prohibited without kernel write-exclusion. "
                        f"Original file preserved AS-IS. Quarantine created at {quarantine_path}. "
                        f"Execute administrative quiescence recovery to truncate."
                    )

        return quarantine_path

    except Exception:
        if tmp_quarantine_path.exists():
            try:
                tmp_quarantine_path.unlink()
            except OSError:
                pass
        raise


# ─── SAFE RECOVERY ───────────────────────────────────────────────────────────

def recover_torn_tail(
    ledger_path: Union[str, Path],
    timeout: float = 10.0,
    _force_posix_mode: bool = False,
) -> Path:
    """Safely quarantine corrupted ledger and truncate torn tail under file lock."""
    l_path = Path(ledger_path)
    with locked_audit_ledger(l_path, timeout=timeout):
        valid_tail_offset, last_seq, last_hash = find_valid_tail_offset(l_path)
        if valid_tail_offset is None:
            raise ForensicQuarantineError(
                f"Ledger {l_path} does not have a truncatable torn tail at EOF. "
                "Automatic recovery is strictly prohibited for middle corruption or missing prefix."
            )
        return execute_verified_forensic_quarantine(
            ledger_path=l_path,
            valid_tail_offset=valid_tail_offset,
            _force_posix_mode=_force_posix_mode,
        )
