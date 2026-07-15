"""Shared classification for credential-bearing durable payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from tianshu.security.redact import redact_text

_SENSITIVE_COMPACT_KEY_ALIASES = frozenset(
    {
        "authorization",
        "authorizationheader",
        "accesstoken",
        "apikey",
        "apisecret",
        "authtoken",
        "cookie",
        "credential",
        "credentials",
        "databaseurl",
        "dbpassword",
        "dburl",
        "masterkey",
        "password",
        "passwd",
        "privatekey",
        "proxyauthorization",
        "refreshtoken",
        "redisurl",
        "secret",
        "secretkey",
        "setcookie",
        "token",
        "xapikey",
        "xauthtoken",
    }
)
_SENSITIVE_COMPACT_KEY_ANCHORS = frozenset(
    {
        "accesstoken",
        "apikey",
        "apisecret",
        "authorization",
        "authtoken",
        "clientsecret",
        "cookievalue",
        "credential",
        "databaseurl",
        "dbpassword",
        "dburl",
        "masterkey",
        "password",
        "passwd",
        "privatekey",
        "refreshtoken",
        "redisurl",
        "secretkey",
        "secretvalue",
        "setcookie",
        "tokenvalue",
    }
)
_SENSITIVE_KEY_TOKEN_SEQUENCES = (
    ("api", "key"),
    ("database", "url"),
    ("db", "url"),
    ("master", "key"),
    ("private", "key"),
    ("redis", "url"),
)
_TOKEN_METRIC_COMPACT_KEY_ALIASES = frozenset(
    {
        "completiontokencount",
        "completiontokens",
        "inputtokencount",
        "inputtokens",
        "maxtokencount",
        "maxtokens",
        "outputtokencount",
        "outputtokens",
        "prompttokencount",
        "prompttokens",
        "tokenbudget",
        "tokencount",
        "totaltokencount",
        "totaltokens",
    }
)
_ENVIRONMENT_COMPACT_KEY_ALIASES = frozenset(
    {"env", "environment", "environmentvalues", "environmentvariables", "envvars"}
)
_SAFE_REDACTED_VALUES = frozenset(
    {
        "[REDACTED]",
        "[REDACTED API KEY]",
        "[REDACTED AWS KEY]",
        "[REDACTED AWS SECRET]",
        "[REDACTED CONNECTION STRING]@",
        "[REDACTED CREDENTIAL]",
        "[REDACTED GITHUB TOKEN]",
        "[REDACTED GITLAB TOKEN]",
        "[REDACTED JWT]",
        "[REDACTED PRIVATE KEY]",
        "[REDACTED SLACK TOKEN]",
        "Bearer [REDACTED]",
    }
)
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SETTINGS_REF = re.compile(r"^settings:[a-z_][a-z0-9_]*$")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def _payload_key_tokens(key: str) -> tuple[str, ...]:
    separated = _CAMEL_CASE_BOUNDARY.sub("_", key)
    normalized = _NON_ALPHANUMERIC.sub("_", separated.casefold())
    return tuple(token for token in normalized.split("_") if token)


def _contains_key_token_sequence(key_tokens: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    width = len(sequence)
    return any(
        key_tokens[index : index + width] == sequence
        for index in range(len(key_tokens) - width + 1)
    )


def _is_json_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_environment_payload_key(key: str) -> bool:
    return "".join(_payload_key_tokens(key)) in _ENVIRONMENT_COMPACT_KEY_ALIASES


def is_sensitive_payload_key(key: str, value: object) -> bool:
    """Classify credential keys without treating numeric token metrics as secrets."""

    key_tokens = _payload_key_tokens(key)
    compact_key = "".join(key_tokens)
    if compact_key in _TOKEN_METRIC_COMPACT_KEY_ALIASES:
        return not _is_json_number(value)
    if any(anchor in compact_key for anchor in _SENSITIVE_COMPACT_KEY_ANCHORS):
        return True
    if any(token in _SENSITIVE_COMPACT_KEY_ALIASES for token in key_tokens):
        return True
    return any(
        _contains_key_token_sequence(key_tokens, sequence)
        for sequence in _SENSITIVE_KEY_TOKEN_SEQUENCES
    )


def is_safe_secret_reference(value: str) -> bool:
    """Accept only a complete environment-name or governed settings reference."""

    return _ENV_NAME.fullmatch(value) is not None or _SETTINGS_REF.fullmatch(value) is not None


def is_safe_redacted_value(value: str) -> bool:
    return value in _SAFE_REDACTED_VALUES


def _contains_known_secret_pattern(value: str) -> bool:
    redacted = redact_text(value)
    return redacted != value and ("[REDACTED" in redacted or "Bearer [REDACTED]" in redacted)


def contains_raw_sensitive_payload(value: object, *, sensitive_context: bool = False) -> bool:
    """Return whether a nested payload contains a raw credential value."""

    if isinstance(value, str):
        if is_safe_redacted_value(value):
            return False
        if sensitive_context and is_safe_secret_reference(value):
            return False
        if sensitive_context:
            return bool(value)
        if "[REDACTED" in value or "Bearer [REDACTED]" in value:
            return True
        return _contains_known_secret_pattern(value)
    if isinstance(value, Mapping):
        return any(
            contains_raw_sensitive_payload(
                item,
                sensitive_context=(
                    sensitive_context
                    or is_environment_payload_key(str(key))
                    or is_sensitive_payload_key(str(key), item)
                ),
            )
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(
            contains_raw_sensitive_payload(item, sensitive_context=sensitive_context)
            for item in value
        )
    return sensitive_context and value is not None


def redact_sensitive_mapping(payload: Mapping[str, object]) -> dict[str, object]:
    """Redact a durable mapping using the shared key classifier."""

    redacted: dict[str, object] = {}
    for key, value in payload.items():
        if is_environment_payload_key(key):
            redacted[key] = (
                {str(environment_key): "[REDACTED]" for environment_key in value}
                if isinstance(value, Mapping)
                else "[REDACTED]"
            )
        elif is_sensitive_payload_key(key, value):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = _redact_sensitive_value(value)
    return redacted


def _redact_sensitive_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_sensitive_mapping({str(key): item for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive_value(item) for item in value]
    return value


__all__ = [
    "contains_raw_sensitive_payload",
    "is_environment_payload_key",
    "is_safe_redacted_value",
    "is_safe_secret_reference",
    "is_sensitive_payload_key",
    "redact_sensitive_mapping",
]
