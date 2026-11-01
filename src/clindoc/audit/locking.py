"""Cross-platform kernel file locking and filesystem durability primitives.

Conforms to CLINICALDOC_STEP3_DEF003_IMPLEMENTATION_EXECUTION_PLAN.md (§3, §4, §6).

Guarantees:
1. Multi-Process Exclusion: Mandatory OS-level file lock on `<ledger>.lock`.
   - Windows: msvcrt.locking / Win32 locking.
   - POSIX: fcntl.flock exclusive non-blocking with polling retry.
2. Durability:
   - f.flush() + os.fsync(f.fileno()) committed before releasing locks.
   - Platform-safe parent directory fsync on POSIX; safe no-op on Windows NTFS.
3. Atomic Temporary File Replacement:
   - Writing to `.tmp.<pid>_<uuid>` -> flush -> fsync -> os.replace -> directory fsync.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
import threading
import time
from typing import Generator, Optional, Union
import uuid

logger = logging.getLogger(__name__)


class AuditFileLockError(Exception):
    """Base exception for audit file locking failures."""


class AuditFileLockTimeoutError(AuditFileLockError):
    """Raised when file lock acquisition times out."""


def flush_and_fsync(file_obj) -> None:
    """Flush stdio buffers and force OS kernel page cache sync to disk."""
    file_obj.flush()
    if hasattr(file_obj, "fileno"):
        os.fsync(file_obj.fileno())


def fsync_directory(dir_path: Union[str, Path]) -> None:
    """Force directory metadata sync on POSIX; safe no-op on Windows NTFS."""
    if os.name != "nt":
        d_path = str(dir_path)
        try:
            dir_fd = os.open(d_path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            logger.debug("Directory fsync on %s omitted/failed: %s", d_path, exc)


def atomic_write_file(
    target_path: Union[str, Path],
    content_bytes: bytes,
    sync_dir: bool = True,
) -> Path:
    """Write bytes to an atomic temporary file, flush, fsync, and rename to target."""
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f"{target.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:8]}")

    try:
        with open(tmp_path, "wb") as f:
            f.write(content_bytes)
            flush_and_fsync(f)
        os.replace(tmp_path, target)
        if sync_dir:
            fsync_directory(target.parent)
        return target
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


class AuditFileLock:
    """Cross-platform kernel file lock on a dedicated lock file.

    Supports timeout, polling interval, and thread-safe re-entrancy within the same process.
    """

    _tls = threading.local()

    def __init__(
        self,
        lock_path: Union[str, Path],
        timeout: float = 10.0,
        poll_interval: float = 0.05,
    ) -> None:
        self.lock_path = Path(lock_path)
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self._fd: Optional[int] = None
        self._file_obj = None

    def _get_reentrancy_count(self) -> int:
        counts = getattr(self._tls, "lock_counts", None)
        if counts is None:
            counts = {}
            self._tls.lock_counts = counts
        return counts.get(str(self.lock_path), 0)

    def _set_reentrancy_count(self, count: int) -> None:
        counts = getattr(self._tls, "lock_counts", None)
        if counts is None:
            counts = {}
            self._tls.lock_counts = counts
        if count <= 0:
            counts.pop(str(self.lock_path), None)
        else:
            counts[str(self.lock_path)] = count

    def acquire(self) -> bool:
        """Acquire the OS kernel file lock, blocking up to timeout seconds."""
        current_depth = self._get_reentrancy_count()
        if current_depth > 0:
            self._set_reentrancy_count(current_depth + 1)
            return True

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()

        # Open file in binary append/read mode for locking
        f = open(self.lock_path, "a+b")
        fd = f.fileno()

        locked = False
        while not locked:
            if os.name == "nt":
                import msvcrt
                try:
                    f.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    locked = True
                except (OSError, IOError):
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except (OSError, IOError):
                    pass

            if locked:
                break

            if time.monotonic() - start_time >= self.timeout:
                f.close()
                raise AuditFileLockTimeoutError(
                    f"Timed out after {self.timeout}s waiting for audit file lock: {self.lock_path}"
                )

            time.sleep(self.poll_interval)

        self._file_obj = f
        self._fd = fd
        self._set_reentrancy_count(1)
        return True

    def release(self) -> None:
        """Release the kernel file lock."""
        current_depth = self._get_reentrancy_count()
        if current_depth > 1:
            self._set_reentrancy_count(current_depth - 1)
            return

        if self._file_obj is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    try:
                        self._file_obj.seek(0)
                        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                    except (OSError, IOError) as exc:
                        logger.debug("Lock release exception on %s: %s", self.lock_path, exc)
                else:
                    import fcntl
                    try:
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                    except (OSError, IOError) as exc:
                        logger.debug("Flock unlock exception on %s: %s", self.lock_path, exc)
            finally:
                try:
                    self._file_obj.close()
                except Exception:
                    pass
                self._file_obj = None
                self._fd = None
                self._set_reentrancy_count(0)

    def __enter__(self) -> AuditFileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


@contextlib.contextmanager
def locked_audit_ledger(
    ledger_path: Union[str, Path],
    timeout: float = 10.0,
    poll_interval: float = 0.05,
) -> Generator[AuditFileLock, None, None]:
    """Context manager acquiring `<ledger_path>.lock`."""
    l_path = Path(ledger_path)
    lock_file = l_path.with_name(f"{l_path.name}.lock")
    lock = AuditFileLock(lock_file, timeout=timeout, poll_interval=poll_interval)
    with lock:
        yield lock
