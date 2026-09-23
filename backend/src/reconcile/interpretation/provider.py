"""Small synchronous DeepSeek adapter with bounded retries and safe telemetry."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Any, Literal

import httpx

from .prompt import (
    MAX_OUTPUT_TOKENS,
    TEMPERATURE,
    CompiledPrompt,
    PromptCompilationError,
    PromptTooLarge,
    compile_prompt,
)
from .schemas import (
    AttemptTelemetry,
    FailureCode,
    InterpretationFailure,
    InterpretationRequest,
    InterpretationResult,
    SemanticValidationError,
    Usage,
    validate_result,
)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
ATTEMPT_TIMEOUT_SECONDS = 20.0
OVERALL_TIMEOUT_SECONDS = 45.0
MAX_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """Context passed to reservation hooks before an individual attempt."""

    attempt: int
    retry_number: int
    requested_model: str


@dataclass(frozen=True, slots=True)
class AttemptEvent:
    """Finalization event for one reservation and its network outcome."""

    context: AttemptContext
    telemetry: AttemptTelemetry
    reservation: object | None
    billing_state: str


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    """A result or explicit failure, with every attempt's non-sensitive telemetry."""

    result: InterpretationResult | None
    failure: InterpretationFailure | None
    attempts: tuple[AttemptTelemetry, ...]

    @property
    def ok(self) -> bool:
        return self.result is not None and self.failure is None

    @property
    def unavailable(self) -> bool:
        return self.failure is not None


ReserveAttempt = Callable[[AttemptContext], object | None]
FinalizeAttempt = Callable[[AttemptEvent], None]
AllocationValidator = Callable[[InterpretationRequest, InterpretationResult], object]
ReservationState = Literal["not_reserved", "reserved", "released", "unknown", "reconciled"]


class DeepSeekProvider:
    """Injectable HTTP adapter; it never discovers or reads credentials itself."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEEPSEEK_BASE_URL,
        enabled: bool = False,
        budget_available: bool = True,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        attempt_timeout: float = ATTEMPT_TIMEOUT_SECONDS,
        reserve_attempt: ReserveAttempt | None = None,
        finalize_attempt: FinalizeAttempt | None = None,
        allocation_validator: AllocationValidator | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be blank")
        if attempt_timeout <= 0:
            raise ValueError("attempt_timeout must be positive")
        normalized_base_url = base_url.rstrip("/")
        if normalized_base_url != DEEPSEEK_BASE_URL:
            raise ValueError("base_url must be https://api.deepseek.com")
        self.api_key = api_key
        self.model = model.strip()
        self.base_url = normalized_base_url
        self.enabled = enabled
        self.budget_available = budget_available
        self.attempt_timeout = attempt_timeout
        self.reserve_attempt = reserve_attempt
        self.finalize_attempt = finalize_attempt
        self.allocation_validator = allocation_validator
        self._owns_client = client is None
        self.client = client or httpx.Client(
            base_url=self.base_url,
            transport=transport,
            timeout=httpx.Timeout(attempt_timeout),
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> DeepSeekProvider:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _body(compiled: CompiledPrompt, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "messages": list(compiled.messages),
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "stream": False,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": TEMPERATURE,
        }

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> Usage:
        usage = body.get("usage")
        if not isinstance(usage, Mapping):
            return Usage()
        details = usage.get("completion_tokens_details")
        if not isinstance(details, Mapping):
            details = {}

        def integer(*names: str) -> int | None:
            for name in names:
                value = usage.get(name)
                if value is None:
                    value = details.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return value
            return None

        return Usage(
            input_tokens=integer("prompt_tokens", "input_tokens"),
            output_tokens=integer("completion_tokens", "output_tokens"),
            provider_cache_tokens=integer(
                "prompt_cache_hit_tokens",
                "cache_hit_tokens",
                "cached_tokens",
            ),
            reasoning_tokens=integer("reasoning_tokens"),
        )

    @staticmethod
    def _response_model(body: Mapping[str, Any]) -> str | None:
        value = body.get("model")
        return value if isinstance(value, str) and 0 < len(value) <= 200 else None

    @staticmethod
    def _message_content(body: Mapping[str, Any]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ValueError("response has no choices")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ValueError("response has no JSON content")
        return content

    @staticmethod
    def _failure_code(status: int) -> tuple[FailureCode, bool, str]:
        if status == 400:
            return FailureCode.BAD_REQUEST, False, "provider rejected the request"
        if status == 401:
            return FailureCode.UNAUTHORIZED, False, "provider credentials were rejected"
        if status == 402:
            return FailureCode.PAYMENT_REQUIRED, False, "provider credit is unavailable"
        if status == 403:
            return FailureCode.FORBIDDEN, False, "provider access was forbidden"
        if status == 404:
            return FailureCode.NOT_FOUND, False, "provider endpoint was not found"
        if status == 422:
            return FailureCode.UNPROCESSABLE, False, "provider rejected the input"
        if status == 429:
            return FailureCode.RATE_LIMITED, True, "provider rate limit reached"
        if status >= 500:
            return FailureCode.SERVER_ERROR, True, "provider server error"
        return FailureCode.HTTP_ERROR, False, "provider returned an HTTP error"

    def _telemetry(
        self,
        context: AttemptContext,
        started: int,
        *,
        response_model: str | None = None,
        usage: Usage | None = None,
        status: int | None = None,
        failure_code: FailureCode | None = None,
        reservation_state: ReservationState = "not_reserved",
    ) -> AttemptTelemetry:
        return AttemptTelemetry(
            attempt=context.attempt,
            retry_number=context.retry_number,
            requested_model=self.model,
            response_model=response_model,
            usage=usage or Usage(),
            latency_ms=(perf_counter_ns() - started) / 1_000_000,
            http_status=status,
            failure_code=failure_code,
            reservation_state=reservation_state,
        )

    def _failure(
        self,
        code: FailureCode,
        message: str,
        context: AttemptContext,
        telemetry: AttemptTelemetry,
        *,
        retryable: bool,
        status: int | None = None,
    ) -> InterpretationFailure:
        return InterpretationFailure(
            code=code,
            message=message,
            retryable=retryable,
            http_status=status,
            attempt=context.attempt,
            retry_number=context.retry_number,
            requested_model=self.model,
            response_model=telemetry.response_model,
            usage=telemetry.usage,
            latency_ms=telemetry.latency_ms,
            reservation_state=telemetry.reservation_state,
        )

    def _finish(
        self,
        finalize: FinalizeAttempt | None,
        context: AttemptContext,
        telemetry: AttemptTelemetry,
        reservation: object | None,
        billing_state: str,
    ) -> None:
        if finalize is not None:
            finalize(AttemptEvent(context, telemetry, reservation, billing_state))

    def interpret(
        self,
        request: InterpretationRequest,
        *,
        reserve_attempt: ReserveAttempt | None = None,
        finalize_attempt: FinalizeAttempt | None = None,
    ) -> ProviderOutcome:
        """Compile, reserve and call with at most one retry.

        Reservation/finalization hooks are called separately for each network
        attempt, including a retry. A transport timeout remains billing-unknown.
        """

        try:
            compiled = compile_prompt(request)
        except PromptTooLarge as exc:
            context = AttemptContext(1, 0, self.model)
            telemetry = self._telemetry(
                context,
                perf_counter_ns(),
                failure_code=FailureCode.PROMPT_TOO_LARGE,
            )
            failure = self._failure(
                FailureCode.PROMPT_TOO_LARGE,
                str(exc),
                context,
                telemetry,
                retryable=False,
            )
            return ProviderOutcome(None, failure, (telemetry,))
        except PromptCompilationError:
            context = AttemptContext(1, 0, self.model)
            telemetry = self._telemetry(
                context,
                perf_counter_ns(),
                failure_code=FailureCode.INVALID_RESPONSE,
            )
            failure = self._failure(
                FailureCode.INVALID_RESPONSE,
                "interpretation request could not be compiled",
                context,
                telemetry,
                retryable=False,
            )
            return ProviderOutcome(None, failure, (telemetry,))

        if not self.enabled:
            context = AttemptContext(1, 0, self.model)
            started = perf_counter_ns()
            telemetry = self._telemetry(context, started, failure_code=FailureCode.DISABLED)
            failure = self._failure(
                FailureCode.DISABLED,
                "live interpretation is disabled",
                context,
                telemetry,
                retryable=False,
            )
            return ProviderOutcome(None, failure, (telemetry,))
        if not self.api_key:
            context = AttemptContext(1, 0, self.model)
            started = perf_counter_ns()
            telemetry = self._telemetry(
                context,
                started,
                failure_code=FailureCode.MISSING_CREDENTIAL,
            )
            failure = self._failure(
                FailureCode.MISSING_CREDENTIAL,
                "provider credential was not supplied",
                context,
                telemetry,
                retryable=False,
            )
            return ProviderOutcome(None, failure, (telemetry,))
        if not self.budget_available:
            context = AttemptContext(1, 0, self.model)
            started = perf_counter_ns()
            telemetry = self._telemetry(
                context,
                started,
                failure_code=FailureCode.BUDGET_UNAVAILABLE,
            )
            failure = self._failure(
                FailureCode.BUDGET_UNAVAILABLE,
                "interpretation budget is unavailable",
                context,
                telemetry,
                retryable=False,
            )
            return ProviderOutcome(None, failure, (telemetry,))

        reserve = reserve_attempt or self.reserve_attempt
        finalize = finalize_attempt or self.finalize_attempt
        attempts: list[AttemptTelemetry] = []
        workflow_started = perf_counter_ns()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            context = AttemptContext(attempt, attempt - 1, self.model)
            reservation: object | None = None
            if reserve is not None:
                try:
                    reservation = reserve(context)
                except Exception:
                    telemetry = self._telemetry(
                        context,
                        perf_counter_ns(),
                        failure_code=FailureCode.BUDGET_UNAVAILABLE,
                    )
                    attempts.append(telemetry)
                    failure = self._failure(
                        FailureCode.BUDGET_UNAVAILABLE,
                        "attempt reservation was unavailable",
                        context,
                        telemetry,
                        retryable=False,
                    )
                    return ProviderOutcome(None, failure, tuple(attempts))

            remaining = (
                OVERALL_TIMEOUT_SECONDS - (perf_counter_ns() - workflow_started) / 1_000_000_000
            )
            if remaining <= 0:
                telemetry = self._telemetry(
                    context,
                    perf_counter_ns(),
                    failure_code=FailureCode.TIMEOUT,
                    reservation_state="released",
                )
                attempts.append(telemetry)
                self._finish(finalize, context, telemetry, reservation, "released")
                failure = self._failure(
                    FailureCode.TIMEOUT,
                    "interpretation workflow deadline exceeded",
                    context,
                    telemetry,
                    retryable=True,
                )
                return ProviderOutcome(None, failure, tuple(attempts))

            started = perf_counter_ns()
            response_model: str | None = None
            usage = Usage()
            try:
                response = self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=self._body(compiled, self.model),
                    timeout=min(self.attempt_timeout, remaining),
                )
            except httpx.TimeoutException:
                telemetry = self._telemetry(
                    context,
                    started,
                    failure_code=FailureCode.TIMEOUT,
                    reservation_state="unknown",
                )
                attempts.append(telemetry)
                self._finish(finalize, context, telemetry, reservation, "unknown")
                if attempt < MAX_ATTEMPTS:
                    continue
                failure = self._failure(
                    FailureCode.TIMEOUT,
                    "provider attempt timed out",
                    context,
                    telemetry,
                    retryable=True,
                )
                return ProviderOutcome(None, failure, tuple(attempts))
            except httpx.TransportError:
                telemetry = self._telemetry(
                    context,
                    started,
                    failure_code=FailureCode.TRANSPORT_ERROR,
                    reservation_state="unknown",
                )
                attempts.append(telemetry)
                self._finish(finalize, context, telemetry, reservation, "unknown")
                if attempt < MAX_ATTEMPTS:
                    continue
                failure = self._failure(
                    FailureCode.TRANSPORT_ERROR,
                    "provider transport failed",
                    context,
                    telemetry,
                    retryable=True,
                )
                return ProviderOutcome(None, failure, tuple(attempts))

            status = int(response.status_code)
            if status < 200 or status >= 300:
                code, retryable, message = self._failure_code(status)
                state: ReservationState = "unknown" if retryable else "released"
                telemetry = self._telemetry(
                    context,
                    started,
                    status=status,
                    failure_code=code,
                    reservation_state=state,
                )
                attempts.append(telemetry)
                self._finish(finalize, context, telemetry, reservation, state)
                if retryable and attempt < MAX_ATTEMPTS:
                    continue
                failure = self._failure(
                    code,
                    message,
                    context,
                    telemetry,
                    retryable=retryable,
                    status=status,
                )
                return ProviderOutcome(None, failure, tuple(attempts))

            try:
                raw_body = response.json()
                if not isinstance(raw_body, Mapping):
                    raise ValueError("response is not an object")
                response_model = self._response_model(raw_body)
                usage = self._usage(raw_body)
                result_payload = json.loads(self._message_content(raw_body))
                result = InterpretationResult.model_validate(result_payload)
                validate_result(
                    request,
                    result,
                    allocation_validator=self.allocation_validator,
                )
            except SemanticValidationError as exc:
                code = (
                    FailureCode.INVALID_CITATION
                    if exc.kind == "citation"
                    else FailureCode.INVALID_ALLOCATION
                    if exc.kind == "allocation"
                    else FailureCode.INVALID_RESPONSE
                )
                billing_state: ReservationState = (
                    "reconciled"
                    if usage.input_tokens is not None and usage.output_tokens is not None
                    else "unknown"
                )
                telemetry = self._telemetry(
                    context,
                    started,
                    response_model=response_model,
                    usage=usage,
                    status=status,
                    failure_code=code,
                    reservation_state=billing_state,
                )
                attempts.append(telemetry)
                self._finish(finalize, context, telemetry, reservation, billing_state)
                failure = self._failure(
                    code,
                    "provider response failed semantic validation",
                    context,
                    telemetry,
                    retryable=False,
                    status=status,
                )
                return ProviderOutcome(None, failure, tuple(attempts))
            except (ValueError, TypeError, json.JSONDecodeError):
                billing_state = (
                    "reconciled"
                    if usage.input_tokens is not None and usage.output_tokens is not None
                    else "unknown"
                )
                telemetry = self._telemetry(
                    context,
                    started,
                    response_model=response_model,
                    usage=usage,
                    status=status,
                    failure_code=FailureCode.INVALID_RESPONSE,
                    reservation_state=billing_state,
                )
                attempts.append(telemetry)
                self._finish(finalize, context, telemetry, reservation, billing_state)
                failure = self._failure(
                    FailureCode.INVALID_RESPONSE,
                    "provider response was not valid interpretation JSON",
                    context,
                    telemetry,
                    retryable=False,
                    status=status,
                )
                return ProviderOutcome(None, failure, tuple(attempts))

            billing_state = (
                "reconciled"
                if usage.input_tokens is not None and usage.output_tokens is not None
                else "unknown"
            )
            telemetry = self._telemetry(
                context,
                started,
                response_model=response_model,
                usage=usage,
                status=status,
                reservation_state=billing_state,
            )
            attempts.append(telemetry)
            self._finish(finalize, context, telemetry, reservation, billing_state)
            return ProviderOutcome(result, None, tuple(attempts))

        # The loop always returns from a terminal branch.
        raise AssertionError("provider attempt loop ended unexpectedly")


DeepSeekClient = DeepSeekProvider
ProviderResult = ProviderOutcome
StructuredFailure = InterpretationFailure


__all__ = [
    "ATTEMPT_TIMEOUT_SECONDS",
    "AttemptContext",
    "AttemptEvent",
    "DEFAULT_MODEL",
    "DEEPSEEK_BASE_URL",
    "DeepSeekClient",
    "DeepSeekProvider",
    "FinalizeAttempt",
    "MAX_ATTEMPTS",
    "OVERALL_TIMEOUT_SECONDS",
    "ProviderOutcome",
    "ProviderResult",
    "ReserveAttempt",
    "StructuredFailure",
]
