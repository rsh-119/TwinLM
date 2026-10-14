"""Structured (JSON / JSON-Schema) output, provider-agnostic.

OpenAI's `response_format` is honored by instruction + validation rather
than each provider's native JSON mode: native support differs per
provider and model, while this works the same everywhere. Flow:

    instruct -> generate -> parse -> validate --ok--> return
                                       |
                                    invalid -> bounded repair turn(s) -> error
"""
import json
import re
from typing import Any, Dict, Optional, Tuple

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import NoSuchResource, Unresolvable

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_MAX_SCHEMA_BYTES = 65_536
_MAX_SCHEMA_NODES = 2_000
_MAX_SCHEMA_DEPTH = 32


def _reject_remote_reference(uri: str):
    raise NoSuchResource(ref=uri)


_OFFLINE_REGISTRY = Registry(retrieve=_reject_remote_reference)


def _check_schema_limits(schema: Dict[str, Any]) -> None:
    try:
        encoded = json.dumps(schema, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid response_format json_schema") from exc
    if len(encoded) > _MAX_SCHEMA_BYTES:
        raise ValueError("response_format json_schema is too large")
    pending = [(schema, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_SCHEMA_NODES or depth > _MAX_SCHEMA_DEPTH:
            raise ValueError("response_format json_schema is too complex")
        if isinstance(value, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in value and (not isinstance(value[key], str) or not value[key].startswith("#")):
                    raise ValueError("external schema references are not supported")
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)


class StructuredOutputError(Exception):
    """The model's output never validated within the repair budget."""


def validator_for(schema: Optional[Dict[str, Any]]) -> Optional[Draft202012Validator]:
    """Raises ValueError for an invalid schema (a client error)."""
    if schema is None:
        return None
    _check_schema_limits(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid response_format json_schema: {exc.message}") from exc
    return Draft202012Validator(schema, registry=_OFFLINE_REGISTRY)


def instruction(schema: Optional[Dict[str, Any]]) -> str:
    text = (
        "Respond with a single valid JSON value and nothing else - no prose, "
        "no explanations, no markdown code fences."
    )
    if schema is not None:
        return text + " The JSON must conform to this JSON Schema:\n" + json.dumps(schema)
    return text + " The top-level value must be a JSON object."


def parse_and_validate(
    content: str, validator: Optional[Draft202012Validator]
) -> Tuple[Optional[str], Optional[str]]:
    """(normalized JSON text, None) if valid, else (None, error message)."""
    match = _FENCE.match(content)
    raw = match.group(1) if match else content.strip()
    try:
        value = json.loads(raw)
    except ValueError as exc:
        return None, f"not valid JSON: {exc}"
    if validator is None:
        if not isinstance(value, dict):
            return None, "top-level JSON value must be an object"
    else:
        try:
            error = next(iter(validator.iter_errors(value)), None)
        except Unresolvable:
            return None, "schema contains an unresolved local reference"
        if error is not None:
            location = "/".join(str(p) for p in error.path) or "(root)"
            return None, f"schema violation at {location}: {error.message}"
    return json.dumps(value, ensure_ascii=False), None


def repair_message(error: str) -> Dict[str, str]:
    return {
        "role": "user",
        "content": f"Your previous reply was invalid ({error}). Reply again with only the corrected JSON.",
    }
