"""
observability/middleware.py — Reusable Microsoft Agent Framework middleware.

Two middlewares are provided:

ContentSafetyMiddleware (AgentMiddleware)
    Pre-call: scans every user / system message in `context.messages` for
    blocked content. Post-call: scans the agent's response.
    Backed by Azure AI Content Safety when AZURE_CONTENT_SAFETY_ENDPOINT
    and AZURE_CONTENT_SAFETY_KEY are set, otherwise a regex blocklist
    fallback is used (suitable for offline tests / local dev).
    On block, the middleware short-circuits the call and returns a
    canned refusal AgentResponse — `call_next()` is never invoked.

AuditFunctionMiddleware (FunctionMiddleware)
    Logs every tool invocation (name, arg keys, duration, status). Pairs
    with OpenTelemetry tracing — OTEL captures spans, this captures human
    readable structured logs.

default_middleware()
    Returns the standard composition used by every agent in this repo.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, List

from agent_framework import (
    AgentContext,
    AgentMiddleware,
    AgentResponse,
    FunctionInvocationContext,
    FunctionMiddleware,
    Message,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default offline blocklist — only used when Azure Content Safety is not
# configured. Conservative: focuses on prompt-injection markers and
# obvious credential / secret leakage patterns.
# ---------------------------------------------------------------------------

_DEFAULT_BLOCKLIST: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bignore\b[^.\n]{0,40}\binstructions?\b"),
    re.compile(r"(?i)\bdisregard\b[^.\n]{0,40}\b(system\s+prompt|instructions?)\b"),
    re.compile(r"(?i)\bsudo\s+rm\s+-rf\b"),
    # AWS access key id
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # GitHub PAT
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    # Generic "api_key=..." with long value
    re.compile(r"(?i)\b(api[_-]?key|secret|password)\s*[=:]\s*[A-Za-z0-9_\-]{20,}\b"),
)


def _scan_blocklist(text: str) -> str | None:
    """Return the first matching pattern label, or None."""
    for pattern in _DEFAULT_BLOCKLIST:
        if pattern.search(text):
            return pattern.pattern
    return None


# ---------------------------------------------------------------------------
# ContentSafetyMiddleware
# ---------------------------------------------------------------------------


class ContentSafetyMiddleware(AgentMiddleware):
    """Block prompt-injection attempts and credential leakage.

    Args:
        threshold: Azure Content Safety severity threshold (0-7) above
            which input/output is blocked. Default 4 (Medium).
        block_on_input: Scan messages before the LLM call. Default True.
        block_on_output: Scan the agent's response. Default True.
    """

    def __init__(
        self,
        *,
        threshold: int = 4,
        block_on_input: bool = True,
        block_on_output: bool = True,
    ) -> None:
        self.threshold = threshold
        self.block_on_input = block_on_input
        self.block_on_output = block_on_output

        self._azure_endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT", "").strip()
        self._azure_key = os.getenv("AZURE_CONTENT_SAFETY_KEY", "").strip()
        self._azure_client = None  # lazy

    # -------- core scan ---------------------------------------------------

    def _scan(self, text: str) -> str | None:
        """Return a reason string if text should be blocked, else None."""
        if not text or not text.strip():
            return None

        if self._azure_endpoint and self._azure_key:
            verdict = self._azure_scan(text)
            if verdict:
                return verdict

        return _scan_blocklist(text)

    def _azure_scan(self, text: str) -> str | None:
        try:
            if self._azure_client is None:
                from azure.ai.contentsafety import ContentSafetyClient
                from azure.core.credentials import AzureKeyCredential
                self._azure_client = ContentSafetyClient(
                    endpoint=self._azure_endpoint,
                    credential=AzureKeyCredential(self._azure_key),
                )
            from azure.ai.contentsafety.models import AnalyzeTextOptions
            resp = self._azure_client.analyze_text(  # type: ignore[union-attr]
                AnalyzeTextOptions(text=text)
            )
            for cat in resp.categories_analysis:
                if cat.severity is not None and cat.severity >= self.threshold:
                    return f"AzureContentSafety:{cat.category}:severity={cat.severity}"
        except Exception as exc:  # pragma: no cover
            logger.warning("AzureContentSafety scan failed: %s — using fallback", exc)
        return None

    # -------- middleware --------------------------------------------------

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        agent_name = getattr(context.agent, "name", "agent")

        # ----- input scan -----
        if self.block_on_input:
            for msg in context.messages or []:
                text = _message_text(msg)
                reason = self._scan(text)
                if reason:
                    logger.warning(
                        "ContentSafety BLOCKED input on %s: %s", agent_name, reason
                    )
                    context.result = _refusal_response(
                        f"Request blocked by content safety policy ({reason})."
                    )
                    return  # short-circuit — do NOT call_next()

        # ----- run the agent -----
        await call_next()

        # ----- output scan -----
        if self.block_on_output and context.result is not None:
            out_text = _response_text(context.result)
            reason = self._scan(out_text)
            if reason:
                logger.warning(
                    "ContentSafety BLOCKED output on %s: %s", agent_name, reason
                )
                context.result = _refusal_response(
                    f"Response blocked by content safety policy ({reason})."
                )


# ---------------------------------------------------------------------------
# AuditFunctionMiddleware
# ---------------------------------------------------------------------------


class AuditFunctionMiddleware(FunctionMiddleware):
    """Structured-log every tool invocation made by an agent.

    Each call produces two log records: ``tool.start`` and
    ``tool.end`` (or ``tool.error``), each with a JSON payload that's
    easy to parse downstream.
    """

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        tool_name = getattr(context.function, "name", "?")
        try:
            arg_keys = list(_args_to_dict(context.arguments).keys())
        except Exception:
            arg_keys = []

        logger.info(
            "tool.start %s",
            json.dumps({"tool": tool_name, "args": arg_keys}, default=str),
        )
        started = time.perf_counter()
        try:
            await call_next()
            logger.info(
                "tool.end %s",
                json.dumps(
                    {
                        "tool": tool_name,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "status": "ok",
                    },
                    default=str,
                ),
            )
        except Exception as exc:
            logger.exception(
                "tool.error %s",
                json.dumps(
                    {
                        "tool": tool_name,
                        "duration_ms": int((time.perf_counter() - started) * 1000),
                        "status": "error",
                        "error": str(exc),
                    },
                    default=str,
                ),
            )
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _message_text(msg: Any) -> str:
    """Best-effort string extraction from a Message / dict / str."""
    if isinstance(msg, str):
        return msg
    if isinstance(msg, Message):
        text = getattr(msg, "text", None)
        if isinstance(text, str):
            return text
        contents = getattr(msg, "contents", None) or []
        return " ".join(
            getattr(c, "text", "") if not isinstance(c, str) else c
            for c in contents
        )
    text = getattr(msg, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(msg, dict):
        return str(msg.get("content") or msg.get("text") or "")
    return str(msg)


def _response_text(result: Any) -> str:
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    return str(result) if result is not None else ""


def _args_to_dict(arguments: Any) -> dict:
    if isinstance(arguments, dict):
        return arguments
    dump = getattr(arguments, "model_dump", None)
    if callable(dump):
        return dump()  # pydantic
    return {}


def _refusal_response(reason: str) -> AgentResponse:
    """Build a synthetic AgentResponse used when content safety blocks."""
    return AgentResponse(
        messages=[Message(role="assistant", contents=[reason])],
    )


# ---------------------------------------------------------------------------
# Public composition
# ---------------------------------------------------------------------------


def default_middleware() -> List[Any]:
    """Standard middleware applied to every agent in this repo."""
    return [ContentSafetyMiddleware(), AuditFunctionMiddleware()]
