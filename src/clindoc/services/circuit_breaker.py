"""Model serving resilience: Circuit Breaker, Concurrency Limiter, and Retry Backoff.

Authority:
  - PROJECT_SPEC.md §3, §12, §13.3
  - PRODUCTIZATION_SPEC.md §3.3, §3.4, §3.5, §13 (Failures F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 (Secure Model Serving Infrastructure)

Implements:
  1. CircuitBreaker:
     - States: CLOSED, OPEN, HALF_OPEN
     - CLOSED -> OPEN after N consecutive qualifying failures
     - OPEN rejects/fails fast without making model requests
     - OPEN -> HALF_OPEN after configurable cool-down
     - HALF_OPEN permits a probe request
     - Probe success -> CLOSED (resets failure counter)
     - Probe failure -> OPEN (resets cool-down timer)
  2. ConcurrencyLimiter:
     - Configurable concurrency cap around calls to model-serving endpoints
     - Bounded semaphore with deterministic timeout/rejection
  3. ExponentialBackoff:
     - Bounded exponential backoff with randomized jitter
     - Configurable base delay and maximum delay
  4. Failure / Retry Classification:
     - Bounded retries strictly for transport errors and schema/parse validation errors
     - Rejects non-retryable errors (4xx, CircuitBreakerOpenError, ConcurrencyLimitExceededError)
"""
from __future__ import annotations

from enum import Enum
import json
import random
import threading
import time
from typing import Any, Callable, Optional, TypeVar

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

try:
    from pydantic import ValidationError
except ImportError:
    ValidationError = None  # type: ignore

from clindoc.observability.metrics import get_metrics

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when an operation is rejected because the circuit breaker is OPEN."""

    def __init__(self, message: str, cooldown_remaining: float = 0.0) -> None:
        super().__init__(message)
        self.cooldown_remaining = max(0.0, float(cooldown_remaining))


class ConcurrencyLimitExceededError(RuntimeError):
    """Raised when maximum model server concurrency limit is exceeded and acquisition times out."""

    def __init__(self, message: str, max_concurrency: int = 1) -> None:
        super().__init__(message)
        self.max_concurrency = int(max_concurrency)


def is_retryable_error(exc: BaseException) -> bool:
    """Classify whether an exception is permitted to be retried under PRODUCTIZATION_SPEC §3.5.

    Permitted retry categories:
      - Transport errors (network disconnect, timeouts, server 5xx, rate limits 429)
      - Schema/parse failures (json.JSONDecodeError, pydantic.ValidationError, schema ValueError)

    Forbidden (fail immediately without retry):
      - CircuitBreakerOpenError (fail fast)
      - ConcurrencyLimitExceededError
      - ProductionProfileError
      - HTTP client 4xx errors (400, 401, 403, 404, etc. except 408/429)
      - Programming/type bugs (TypeError, KeyError, etc.)
    """
    if isinstance(exc, (CircuitBreakerOpenError, ConcurrencyLimitExceededError)):
        return False

    # Check for ProductionProfileError without importing to avoid cycle
    if exc.__class__.__name__ == "ProductionProfileError":
        return False

    # Transport / HTTP errors
    if httpx is not None:
        if isinstance(exc, httpx.TimeoutException):
            return True
        if isinstance(exc, httpx.NetworkError):
            return True
        if isinstance(exc, httpx.TransportError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code in (408, 429) or code >= 500:
                return True
            return False

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    # Schema and parsing errors
    if isinstance(exc, json.JSONDecodeError):
        return True
    if ValidationError is not None and isinstance(exc, ValidationError):
        return True
    if isinstance(exc, ValueError):
        msg = str(exc).lower()
        if any(keyword in msg for keyword in ("contradiction_type", "schema", "json", "invalid", "extractions")):
            return True

    return False


def is_qualifying_breaker_failure(exc: BaseException) -> bool:
    """Classify whether an error counts as a model server failure that trips the circuit breaker."""
    if isinstance(exc, (CircuitBreakerOpenError, ConcurrencyLimitExceededError)):
        return False

    if exc.__class__.__name__ == "ProductionProfileError":
        return False

    # Transport / HTTP server errors
    if httpx is not None:
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code >= 500 or code in (408, 429):
                return True
            return False

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    # Malformed output from model also indicates model serving failure
    if isinstance(exc, json.JSONDecodeError):
        return True
    if ValidationError is not None and isinstance(exc, ValidationError):
        return True

    return False


# ─── Structured Fallback Classification (Phase 8.7 Step 2) ───────────────────

FALLBACK_REASON_LLM_UNAVAILABLE: str = "llm_unavailable"
FALLBACK_REASON_LLM_SCHEMA_INVALID: str = "llm_schema_invalid"


def classify_fallback_reason(exc: Optional[BaseException]) -> Optional[str]:
    """Classify the root cause of an LLM generation/validation failure into a machine-readable code.

    Authority:
      - PRODUCTIZATION_SPEC.md §3.5, §13 (Failures F-06, F-07)
      - DEC-013: Grammar-constrained structured output enforcement

    Returns:
      - 'llm_schema_invalid' if the output failed JSON decoding, Pydantic validation,
        or schema invariant verification (F-07).
      - 'llm_unavailable' for transport disconnects, timeouts, HTTP 5xx, HTTP 429,
        CircuitBreakerOpenError, ConcurrencyLimitExceededError, or service unreachability (F-06).
      - None if exc is None.
    """
    if exc is None:
        return None

    # Schema, JSON parsing, and invariant failures (F-07)
    if isinstance(exc, json.JSONDecodeError):
        return FALLBACK_REASON_LLM_SCHEMA_INVALID

    if ValidationError is not None and isinstance(exc, ValidationError):
        return FALLBACK_REASON_LLM_SCHEMA_INVALID

    if isinstance(exc, ValueError):
        msg = str(exc).lower()
        if any(keyword in msg for keyword in ("contradiction_type", "schema", "json", "invalid", "extractions", "stage_c_judge_output")):
            return FALLBACK_REASON_LLM_SCHEMA_INVALID

    # All transport, connection, timeout, circuit breaker, concurrency cap, or HTTP 5xx/429 map to llm_unavailable (F-06)
    return FALLBACK_REASON_LLM_UNAVAILABLE


class CircuitBreaker:
    """Production-grade circuit breaker with CLOSED, OPEN, and HALF_OPEN states.

    Invariants:
      - Initial state: CLOSED, failure_count = 0.
      - CLOSED -> OPEN after failure_threshold consecutive qualifying failures.
      - In OPEN state, calls fail fast with CircuitBreakerOpenError without invoking backend.
      - OPEN -> HALF_OPEN after cooldown_seconds have elapsed.
      - HALF_OPEN permits a probe request.
      - Probe success -> CLOSED (resets failure count and probe flag).
      - Probe failure -> OPEN (resets cooldown timer and probe flag).
    """

    def __init__(
        self,
        name: str = "vllm_gateway",
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        time_func: Optional[Callable[[], float]] = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold must be >= 1, got {failure_threshold}")
        if cooldown_seconds <= 0.0:
            raise ValueError(f"cooldown_seconds must be > 0.0, got {cooldown_seconds}")

        self.name = name
        self.failure_threshold = int(failure_threshold)
        self.cooldown_seconds = float(cooldown_seconds)
        self._time_func = time_func or time.monotonic

        self._lock = threading.RLock()
        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_probe_in_progress: bool = False
        get_metrics().set_circuit_breaker_state(self.name, "CLOSED")

    @property
    def state(self) -> CircuitState:
        """Current state of the circuit breaker, accounting for cool-down expiration."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                now = self._time_func()
                if self._last_failure_time is not None:
                    elapsed = now - self._last_failure_time
                    if elapsed >= self.cooldown_seconds:
                        self._state = CircuitState.HALF_OPEN
                        self._half_open_probe_in_progress = False
                        get_metrics().record_circuit_breaker_transition(self.name, "half_open")
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    @property
    def last_failure_time(self) -> Optional[float]:
        with self._lock:
            return self._last_failure_time

    def can_execute(self) -> bool:
        """Check if a request is permitted to proceed."""
        with self._lock:
            cur_state = self.state
            if cur_state == CircuitState.CLOSED:
                return True
            if cur_state == CircuitState.HALF_OPEN:
                return not self._half_open_probe_in_progress
            return False

    def before_call(self) -> None:
        """Verify the breaker permits an execution; raise CircuitBreakerOpenError if blocked."""
        with self._lock:
            cur_state = self.state
            now = self._time_func()

            if cur_state == CircuitState.OPEN:
                elapsed = (now - self._last_failure_time) if self._last_failure_time is not None else 0.0
                remaining = max(0.0, self.cooldown_seconds - elapsed)
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is OPEN. Model server unavailable. "
                    f"Cool-down remaining: {remaining:.1f}s",
                    cooldown_remaining=remaining,
                )

            if cur_state == CircuitState.HALF_OPEN:
                if self._half_open_probe_in_progress:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is HALF_OPEN and probe is already in progress.",
                        cooldown_remaining=self.cooldown_seconds,
                    )
                self._half_open_probe_in_progress = True

    def record_success(self) -> None:
        """Record successful execution. Resets consecutive failure counter and closes circuit."""
        with self._lock:
            prev_state = self._state
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._last_failure_time = None
            self._half_open_probe_in_progress = False
            if prev_state != CircuitState.CLOSED:
                get_metrics().record_circuit_breaker_transition(self.name, "closed")

    def record_failure(self, exc: Optional[BaseException] = None) -> None:
        """Record an execution failure."""
        with self._lock:
            if exc is not None and not is_qualifying_breaker_failure(exc):
                # If in HALF_OPEN and error was not qualifying, reset probe in progress
                if self._state == CircuitState.HALF_OPEN:
                    self._half_open_probe_in_progress = False
                return

            now = self._time_func()
            cur_state = self.state

            if cur_state == CircuitState.HALF_OPEN:
                # Probe failed: immediately trip back to OPEN
                self._state = CircuitState.OPEN
                self._last_failure_time = now
                self._half_open_probe_in_progress = False
                self._consecutive_failures += 1
                get_metrics().record_circuit_breaker_transition(self.name, "open")
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_failure_time = now
                    get_metrics().record_circuit_breaker_transition(self.name, "open")

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute a callable protected by the circuit breaker."""
        with self:
            return func(*args, **kwargs)

    def __enter__(self) -> "CircuitBreaker":
        self.before_call()
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        if exc_val is None:
            self.record_success()
        else:
            self.record_failure(exc_val)


class ConcurrencyLimiter:
    """Thread-safe bounded concurrency limiter for model serving endpoints.

    Limits the number of concurrent in-flight requests to prevent GPU server overload.
    """

    def __init__(
        self,
        max_concurrency: int = 10,
        timeout: Optional[float] = 30.0,
        name: str = "qwen",
    ) -> None:
        if max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {max_concurrency}")
        self.name = name
        self.max_concurrency = int(max_concurrency)
        self.timeout = timeout
        self._semaphore = threading.BoundedSemaphore(value=self.max_concurrency)
        self._active_count = 0
        self._waiting_count = 0
        self._lock = threading.Lock()

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active_count

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Acquire a concurrency permit. Waits up to timeout seconds."""
        to = self.timeout if timeout is None else timeout
        with self._lock:
            self._waiting_count += 1
            get_metrics().set_concurrency_waiting(self.name, self._waiting_count)
        try:
            acquired = self._semaphore.acquire(blocking=True, timeout=to)
            with self._lock:
                self._waiting_count = max(0, self._waiting_count - 1)
                get_metrics().set_concurrency_waiting(self.name, self._waiting_count)
                if acquired:
                    self._active_count += 1
                    get_metrics().set_concurrency_permits(self.name, self._active_count)
            return acquired
        except Exception:
            with self._lock:
                self._waiting_count = max(0, self._waiting_count - 1)
                get_metrics().set_concurrency_waiting(self.name, self._waiting_count)
            raise

    def release(self) -> None:
        """Release an acquired concurrency permit."""
        with self._lock:
            if self._active_count > 0:
                self._active_count -= 1
            get_metrics().set_concurrency_permits(self.name, self._active_count)
        self._semaphore.release()

    def __enter__(self) -> "ConcurrencyLimiter":
        if not self.acquire():
            get_metrics().record_concurrency_wait_timeout(self.name)
            raise ConcurrencyLimitExceededError(
                f"Model server concurrency limit ({self.max_concurrency}) exceeded. "
                f"Acquisition timed out after {self.timeout}s.",
                max_concurrency=self.max_concurrency,
            )
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        self.release()


class ExponentialBackoff:
    """Exponential backoff with randomized jitter per PRODUCTIZATION_SPEC §3.5.

    Delay formula for attempt k (1-indexed retry count, k=1, 2, ...):
      delay_raw = min(max_delay, base_delay * (factor ** (k - 1)))
      delay = random.uniform(0.0, delay_raw) if jitter else delay_raw
    """

    def __init__(
        self,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        factor: float = 2.0,
        jitter: bool = True,
        random_func: Optional[Callable[[float, float], float]] = None,
        sleep_func: Optional[Callable[[float], None]] = None,
    ) -> None:
        if base_delay <= 0.0:
            raise ValueError(f"base_delay must be > 0.0, got {base_delay}")
        if max_delay < base_delay:
            raise ValueError(
                f"max_delay must be >= base_delay, got max_delay={max_delay}, base_delay={base_delay}"
            )
        if factor < 1.0:
            raise ValueError(f"factor must be >= 1.0, got {factor}")

        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self.factor = float(factor)
        self.jitter = bool(jitter)
        self.random_func = random_func or random.uniform
        self.sleep_func = sleep_func or time.sleep

    def compute_delay(self, attempt: int) -> float:
        """Compute the backoff delay for the given retry attempt (1-indexed)."""
        k = max(1, int(attempt))
        raw_delay = min(self.max_delay, self.base_delay * (self.factor ** (k - 1)))
        if self.jitter:
            return float(self.random_func(0.0, raw_delay))
        return float(raw_delay)

    def sleep(self, attempt: int) -> float:
        """Compute delay and perform sleep. Returns the sleep duration."""
        delay = self.compute_delay(attempt)
        if delay > 0.0:
            self.sleep_func(delay)
        return delay


# ─── Global Registries for Shared Breakers/Limiters ──────────────────────────

_CIRCUIT_BREAKER_REGISTRY: dict[str, CircuitBreaker] = {}
_CONCURRENCY_LIMITER_REGISTRY: dict[str, ConcurrencyLimiter] = {}
_REGISTRY_LOCK = threading.Lock()


def get_shared_circuit_breaker(
    endpoint: str = "default",
    failure_threshold: int = 5,
    cooldown_seconds: float = 30.0,
) -> CircuitBreaker:
    """Get or create a shared circuit breaker for a given endpoint."""
    with _REGISTRY_LOCK:
        if endpoint not in _CIRCUIT_BREAKER_REGISTRY:
            _CIRCUIT_BREAKER_REGISTRY[endpoint] = CircuitBreaker(
                name=f"cb_{endpoint}",
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
        return _CIRCUIT_BREAKER_REGISTRY[endpoint]


def get_shared_concurrency_limiter(
    endpoint: str = "default",
    max_concurrency: int = 10,
    timeout: Optional[float] = 30.0,
) -> ConcurrencyLimiter:
    """Get or create a shared concurrency limiter for a given endpoint."""
    with _REGISTRY_LOCK:
        if endpoint not in _CONCURRENCY_LIMITER_REGISTRY:
            _CONCURRENCY_LIMITER_REGISTRY[endpoint] = ConcurrencyLimiter(
                max_concurrency=max_concurrency,
                timeout=timeout,
            )
        return _CONCURRENCY_LIMITER_REGISTRY[endpoint]


def reset_shared_registries() -> None:
    """Reset shared registries (primarily used for unit testing)."""
    with _REGISTRY_LOCK:
        _CIRCUIT_BREAKER_REGISTRY.clear()
        _CONCURRENCY_LIMITER_REGISTRY.clear()
