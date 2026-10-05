"""Database-backed asynchronous job queue, leasing, and worker state machine.

Authority:
  - PRODUCTIZATION_SPEC.md §2, §4.1 (R-3), §4.2, §4.3, §9.2, §13 (F-17), §16
  - PRODUCTIZATION_DECISIONS.md PD-001, PD-007, §7 Item 3

Implements:
  - Atomic job claiming via PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`
  - Clean SQLite compatibility fallback for testing/development
  - Lease management and reaper for worker crash recovery (F-17)
  - Strict state-machine transition validation (PENDING -> RUNNING -> COMPLETED/FAILED/PENDING)
  - Configurable operational defaults for lease duration and retry bounds
  - Telemetry recording primitives for JobStageEvent
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from clindoc.config import (
    DEFAULT_JOB_LEASE_SECONDS,
    DEFAULT_JOB_MAX_ATTEMPTS,
    get_job_lease_seconds,
)
from clindoc.db.enums import JobStatus
from clindoc.db.models import JobStageEvent, ProcessingJob


class JobQueueError(Exception):
    """Base exception for all queue and state machine errors."""
    pass


class JobNotFoundError(JobQueueError):
    """Raised when a specified job_id cannot be found."""
    pass


class InvalidStateTransitionError(JobQueueError):
    """Raised when attempting an invalid or illegal job status transition."""
    pass


# Authoritative state machine transitions (PRODUCTIZATION_SPEC.md §4.3, §13 F-17)
# Terminal states: COMPLETED and FAILED have no valid exit transitions.
VALID_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.RUNNING},
    JobStatus.RUNNING: {JobStatus.COMPLETED, JobStatus.PENDING, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
}


class JobQueue:
    """PostgreSQL-backed transactional job queue with lease management.

    Supports atomic job claiming via `FOR UPDATE SKIP LOCKED` in PostgreSQL,
    with an explicit fallback for SQLite in local test environments.
    """

    def __init__(self, default_lease_seconds: Optional[int] = None) -> None:
        self.default_lease_seconds = (
            default_lease_seconds
            if default_lease_seconds is not None and default_lease_seconds > 0
            else get_job_lease_seconds()
        )

    @staticmethod
    def validate_transition(
        current_status: JobStatus | str,
        target_status: JobStatus | str,
        job_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Validate whether transition from current_status to target_status is permitted.

        Raises InvalidStateTransitionError if the transition is prohibited.
        """
        curr = JobStatus(current_status)
        target = JobStatus(target_status)
        allowed = VALID_JOB_TRANSITIONS.get(curr, set())
        if target not in allowed:
            id_str = f" for job {job_id}" if job_id else ""
            raise InvalidStateTransitionError(
                f"Illegal job state transition{id_str}: '{curr.value}' -> '{target.value}'. "
                f"Allowed transitions from '{curr.value}': {[s.value for s in allowed]}."
            )

    @staticmethod
    def build_claim_query(dialect_name: str = "postgresql"):
        """Construct the SQL query for claiming the next pending job.

        For PostgreSQL:
            Emits `FOR UPDATE SKIP LOCKED` to ensure strict atomic concurrency isolation
            between multiple background workers (PD-001, PD-007).
        For SQLite / others:
            Emits standard SELECT (clean test fallback; SQLite does not support SKIP LOCKED).
        """
        stmt = (
            select(ProcessingJob)
            .where(ProcessingJob.status == JobStatus.PENDING)
            .order_by(ProcessingJob.created_at)
            .limit(1)
        )
        if dialect_name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return stmt

    def enqueue(
        self,
        session: Session,
        document_id: uuid.UUID,
        model_run_id: Optional[uuid.UUID] = None,
        max_attempts: Optional[int] = None,
        parent_job_id: Optional[uuid.UUID] = None,
    ) -> ProcessingJob:
        """Add a new document processing job to the queue in PENDING state."""
        eff_max_attempts = (
            max_attempts
            if max_attempts is not None and max_attempts > 0
            else DEFAULT_JOB_MAX_ATTEMPTS
        )

        job = ProcessingJob(
            document_id=document_id,
            model_run_id=model_run_id,
            status=JobStatus.PENDING,
            current_stage="INIT",
            attempts=0,
            max_attempts=eff_max_attempts,
            parent_job_id=parent_job_id,
        )
        session.add(job)
        session.flush()
        return job

    def claim_next(
        self,
        session: Session,
        worker_id: str,
        lease_seconds: Optional[int] = None,
    ) -> Optional[ProcessingJob]:
        """Atomically claim the next pending job from the queue.

        Uses PostgreSQL `FOR UPDATE SKIP LOCKED` in production.
        In SQLite test environments, uses standard table query.

        On successful claim:
          - Validates transition PENDING -> RUNNING
          - Sets status -> RUNNING
          - Sets worker_id -> supplied worker identity
          - Increments attempts -> attempts + 1
          - Sets started_at -> current UTC timestamp
          - Sets lease_expires_at -> current UTC timestamp + lease duration
        """
        if not worker_id or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string identifying the claiming worker.")

        bind = session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else "unknown"

        stmt = self.build_claim_query(dialect_name=dialect_name)
        job = session.scalars(stmt).first()

        if job is None:
            return None

        # Enforce state machine transition
        self.validate_transition(job.status, JobStatus.RUNNING, job_id=job.id)

        now = datetime.now(timezone.utc)
        dur = lease_seconds if (lease_seconds is not None and lease_seconds > 0) else self.default_lease_seconds

        job.status = JobStatus.RUNNING
        job.worker_id = worker_id.strip()
        job.attempts += 1
        job.started_at = now
        job.lease_expires_at = now + timedelta(seconds=dur)
        session.flush()
        return job

    def mark_completed(self, session: Session, job_id: uuid.UUID) -> ProcessingJob:
        """Mark an actively running job as COMPLETED (terminal state).

        Clears lease_expires_at and records completed_at timestamp.
        """
        job = session.get(ProcessingJob, job_id)
        if job is None:
            raise JobNotFoundError(f"ProcessingJob with id {job_id} not found.")

        self.validate_transition(job.status, JobStatus.COMPLETED, job_id=job.id)

        now = datetime.now(timezone.utc)
        job.status = JobStatus.COMPLETED
        job.completed_at = now
        job.lease_expires_at = None
        session.flush()
        return job

    def mark_failed(
        self,
        session: Session,
        job_id: uuid.UUID,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        retryable: bool = False,
    ) -> ProcessingJob:
        """Mark a job as failed, with retry support if designated retryable.

        If retryable is True and attempts < max_attempts:
          - Transitions back to PENDING (re-queued)
          - Clears worker_id and lease_expires_at
          - Preserves attempt count
        Else (terminal failure or max_attempts exhausted):
          - Transitions to FAILED (terminal state)
          - Records error_code, error_message, and completed_at
          - Clears lease_expires_at
        """
        job = session.get(ProcessingJob, job_id)
        if job is None:
            raise JobNotFoundError(f"ProcessingJob with id {job_id} not found.")

        target_status = (
            JobStatus.PENDING
            if (retryable and job.attempts < job.max_attempts)
            else JobStatus.FAILED
        )

        self.validate_transition(job.status, target_status, job_id=job.id)

        now = datetime.now(timezone.utc)
        job.status = target_status
        job.error_code = error_code
        job.error_message = error_message
        job.lease_expires_at = None

        if target_status == JobStatus.PENDING:
            # Requeue for retry
            job.worker_id = None
        else:
            # Terminal failure
            job.completed_at = now

        session.flush()
        return job

    def release_or_requeue(
        self,
        session: Session,
        job_id: uuid.UUID,
        error_message: Optional[str] = None,
    ) -> ProcessingJob:
        """Explicitly release or re-queue a running job (e.g. on graceful shutdown)."""
        return self.mark_failed(
            session=session,
            job_id=job_id,
            error_code="WORKER_RELEASED",
            error_message=error_message or "Job explicitly released by worker for re-queue",
            retryable=True,
        )

    def reclaim_expired_leases(
        self,
        session: Session,
        reference_time: Optional[datetime] = None,
    ) -> List[ProcessingJob]:
        """Scan and recover orphaned jobs whose worker lease has expired (F-17 crash recovery).

        For any RUNNING job where lease_expires_at <= reference_time:
          - If attempts < max_attempts:
              Re-queues the job to PENDING so another worker can claim it.
              Clears worker_id and lease_expires_at.
          - If attempts >= max_attempts:
              Terminally marks the job as FAILED with error_code 'LEASE_EXPIRED_MAX_ATTEMPTS'.
              Clears lease_expires_at and sets completed_at.

        Returns list of all reclaimed jobs.
        """
        now = reference_time or datetime.now(timezone.utc)

        stmt = (
            select(ProcessingJob)
            .where(
                ProcessingJob.status == JobStatus.RUNNING,
                ProcessingJob.lease_expires_at.is_not(None),
                ProcessingJob.lease_expires_at <= now,
            )
        )
        expired_jobs = list(session.scalars(stmt).all())
        reclaimed: List[ProcessingJob] = []

        for job in expired_jobs:
            if job.attempts < job.max_attempts:
                # Requeue
                self.validate_transition(job.status, JobStatus.PENDING, job_id=job.id)
                job.status = JobStatus.PENDING
                job.worker_id = None
                job.lease_expires_at = None
                job.error_message = f"Lease expired after attempt {job.attempts}; re-queued for retry."
            else:
                # Terminal failure
                self.validate_transition(job.status, JobStatus.FAILED, job_id=job.id)
                job.status = JobStatus.FAILED
                job.worker_id = None
                job.lease_expires_at = None
                job.error_code = "LEASE_EXPIRED_MAX_ATTEMPTS"
                job.error_message = f"Worker lease expired and max attempts ({job.max_attempts}) exhausted."
                job.completed_at = now

            reclaimed.append(job)

        if reclaimed:
            session.flush()

        return reclaimed

    def record_stage_event(
        self,
        session: Session,
        job_id: uuid.UUID,
        stage_name: str,
        status: str,
        duration_ms: Optional[int] = None,
        metrics: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> JobStageEvent:
        """Record an execution stage transition and telemetry event into job_stage_events.

        Provides the primitive needed for worker observability (§4.3, §11)
        without requiring direct pipeline execution logic here.
        """
        event = JobStageEvent(
            job_id=job_id,
            stage_name=stage_name,
            status=status,
            duration_ms=duration_ms,
            metrics=metrics or {},
            error_message=error_message,
        )
        session.add(event)
        session.flush()
        return event
