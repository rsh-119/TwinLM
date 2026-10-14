"""Pluggable input/output guardrails, provider-agnostic.

Each guardrail scans text and returns findings. What happens next is
configured per guardrail and stage in gateway.toml:

    block  - reject (input: 400; non-streamed output: 502)
    redact - replace each finding with [REDACTED_<LABEL>] and continue
    log    - continue unchanged, but count and log it (label only, never
             the matched text)

Detection is regex/heuristic based - cheap and predictable, and also
deliberately incomplete: it catches common credential formats, obvious
PII and textbook injection phrasing, not adversarial inputs. A model-based
classifier can be added as another `Guardrail` without touching callers.
"""
import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.gateway.config import GuardrailsConfig, get_gateway_config
from app.observability import GUARDRAIL_VIOLATIONS
from app.providers.base import text_of

logger = logging.getLogger("twinlm.guardrails")


@dataclass(frozen=True)
class Finding:
    guardrail: str
    label: str
    start: int
    end: int


class GuardrailViolation(Exception):
    def __init__(self, stage: str, findings: Sequence[Finding]) -> None:
        self.stage = stage
        self.findings = list(findings)
        labels = sorted({f"{f.guardrail}:{f.label}" for f in findings})
        super().__init__(f"{stage} blocked by guardrail ({', '.join(labels)})")


class Guardrail:
    name: str = ""
    redactable: bool = True
    # roles whose content this guardrail inspects on input
    input_roles: Tuple[str, ...] = ("system", "user", "assistant", "tool")

    def scan(self, text: str) -> List[Finding]:
        raise NotImplementedError


class _RegexGuardrail(Guardrail):
    patterns: Dict[str, re.Pattern] = {}

    def accept(self, label: str, match: re.Match) -> bool:
        return True

    def scan(self, text: str) -> List[Finding]:
        return [
            Finding(self.name, label, m.start(), m.end())
            for label, pattern in self.patterns.items()
            for m in pattern.finditer(text)
            if self.accept(label, m)
        ]


class SecretGuardrail(_RegexGuardrail):
    name = "secrets"
    patterns = {
        "private_key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
        "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
        "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
        "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
        "api_key": re.compile(r"\b(?:sk|gsk|nvapi)-[A-Za-z0-9_\-]{20,}\b"),
        "gateway_key": re.compile(r"\bgw_live_[A-Za-z0-9_\-]{20,}"),
        "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    }


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


class PIIGuardrail(_RegexGuardrail):
    name = "pii"
    patterns = {
        "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "credit_card": re.compile(r"\b(?:\d[ \-]?){13,19}\b"),
        "us_ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "phone": re.compile(r"(?<!\w)\+?\d{1,3}[ .\-]?\(?\d{3}\)?[ .\-]?\d{3}[ .\-]?\d{4}\b"),
    }

    def accept(self, label: str, match: re.Match) -> bool:
        if label == "credit_card":
            digits = re.sub(r"\D", "", match.group())
            return 13 <= len(digits) <= 19 and _luhn_valid(digits)
        return True


class PromptInjectionGuardrail(_RegexGuardrail):
    name = "prompt_injection"
    redactable = False
    input_roles = ("user",)  # the client's own system prompt is trusted
    patterns = {
        "ignore_instructions": re.compile(
            r"(?i)\b(?:ignore|disregard|forget)\b.{0,30}\b(?:previous|prior|above|earlier|all|system)\b.{0,20}\b(?:instructions?|prompts?|rules)\b"
        ),
        "reveal_system_prompt": re.compile(
            r"(?i)\b(?:reveal|print|show|repeat|output)\b.{0,30}\b(?:system prompt|hidden instructions|initial instructions)\b"
        ),
        "role_override": re.compile(r"(?i)\byou are now\b.{0,40}\b(?:DAN|jailbroken|unrestricted|developer mode)\b"),
    }


GUARDRAILS: Dict[str, Guardrail] = {
    g.name: g for g in (SecretGuardrail(), PIIGuardrail(), PromptInjectionGuardrail())
}


def _redact(text: str, findings: Sequence[Finding]) -> str:
    # right-to-left so earlier offsets stay valid; skip overlaps
    out, cursor = text, len(text) + 1
    for f in sorted(findings, key=lambda f: f.start, reverse=True):
        if f.end > cursor:
            continue
        out = out[: f.start] + f"[REDACTED_{f.label.upper()}]" + out[f.end:]
        cursor = f.start
    return out


class GuardrailEngine:
    def __init__(self, config: GuardrailsConfig) -> None:
        unknown = (set(config.input) | set(config.output)) - set(GUARDRAILS)
        if unknown:
            raise ValueError(f"unknown guardrails in config: {sorted(unknown)}")
        for name, action in {**config.input, **config.output}.items():
            if action == "redact" and not GUARDRAILS[name].redactable:
                raise ValueError(f"guardrail {name!r} cannot redact; use block or log")
        self._input = config.input
        self._output = config.output
        self._max_input_chars = config.max_input_chars

    def _apply(self, stage: str, actions: Dict[str, str], text: str, role: Optional[str]) -> str:
        blocking: List[Finding] = []
        to_redact: List[Finding] = []
        for name, action in actions.items():
            guardrail = GUARDRAILS[name]
            if role is not None and role not in guardrail.input_roles:
                continue
            findings = guardrail.scan(text)
            if not findings:
                continue
            for label in sorted({f.label for f in findings}):
                GUARDRAIL_VIOLATIONS.labels(name, stage, action).inc()
                logger.warning("guardrail finding: stage=%s guardrail=%s label=%s action=%s", stage, name, label, action)
            if action == "block":
                blocking.extend(findings)
            elif action == "redact":
                to_redact.extend(findings)
        if blocking:
            raise GuardrailViolation(stage, blocking)
        return _redact(text, to_redact) if to_redact else text

    def _check_content(self, content: Any, role: str) -> Any:
        """Text only: a string, or each text part of a content-part list."""
        if isinstance(content, str):
            return self._apply("input", self._input, content, role)
        if isinstance(content, list):
            return [
                {**p, "text": self._apply("input", self._input, p["text"], role)} if p.get("type") == "text" else p
                for p in content
            ]
        return content

    def _check_nested_text(self, value: Any, role: str) -> Any:
        """Inspect text in tool calls and definitions as well as message content."""
        if isinstance(value, str):
            return self._apply("input", self._input, value, role)
        if isinstance(value, list):
            return [self._check_nested_text(item, role) for item in value]
        if isinstance(value, dict):
            return {key: self._check_nested_text(item, role) for key, item in value.items()}
        return value

    def check_auxiliary(self, value: Any) -> Any:
        """Apply the input policy to tool definitions and format metadata."""
        if value is None:
            return None
        if self._max_input_chars is not None and len(json.dumps(value)) > self._max_input_chars:
            raise GuardrailViolation("input", [Finding("max_input_chars", "too_large", 0, 0)])
        return self._check_nested_text(value, "user")

    def check_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Returns the messages to send (possibly redacted) or raises
        GuardrailViolation."""
        if self._max_input_chars is not None:
            size = sum(len(_all_text(m)) for m in messages)
            if size > self._max_input_chars:
                GUARDRAIL_VIOLATIONS.labels("max_input_chars", "input", "block").inc()
                raise GuardrailViolation("input", [Finding("max_input_chars", "too_large", 0, size)])
        checked = []
        for message in messages:
            item = dict(message)
            if "content" in item:
                item["content"] = self._check_content(item["content"], item["role"])
            if item.get("tool_calls"):
                item["tool_calls"] = self._check_nested_text(item["tool_calls"], item["role"])
            checked.append(item)
        return checked

    def check_output(self, text: str) -> str:
        return self._apply("output", self._output, text, None)

    @staticmethod
    def contains_sensitive(messages: List[Dict[str, str]]) -> bool:
        """Secrets or PII anywhere in the request, regardless of configured
        actions - used to keep such requests out of the response cache."""
        return any(
            GUARDRAILS[name].scan(_all_text(m)) for name in ("secrets", "pii") for m in messages
        )


def _all_text(message: Dict[str, Any]) -> str:
    """Message text plus any tool-call arguments it carries."""
    calls = message.get("tool_calls")
    return text_of(message.get("content")) + (json.dumps(calls) if calls else "")


@lru_cache
def get_guardrails() -> GuardrailEngine:
    return GuardrailEngine(get_gateway_config().guardrails)
