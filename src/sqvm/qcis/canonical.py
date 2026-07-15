"""Canonical bytes and numeric tokens used by the QCIS profile."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

from sqvm.qcis.errors import QCISCompilationError, QCISReasonCode


REAL_TOKEN = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE]-?(0|[1-9][0-9]*))?$")
INTEGER_TOKEN = re.compile(r"0|[1-9][0-9]*$")
SIGNED_INTEGER_TOKEN = re.compile(r"-?(0|[1-9][0-9]*)$")
PLACEHOLDER_TOKEN = re.compile(r"\$[a-z][a-z0-9_]{0,63}$")
OPERATION_TOKEN = re.compile(r"[A-Z][A-Z0-9]*$")
QAGENT_TOKEN = re.compile(r"[A-Z][A-Z0-9]*$")


def canonical_json_bytes(value: Mapping[str, Any] | list[Any]) -> bytes:
    """Return the compact canonical JSON/LF byte form frozen for QCIS evidence."""

    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def sha256_json(value: Mapping[str, Any] | list[Any]) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def parse_real_token(token: str) -> float:
    if not isinstance(token, str) or REAL_TOKEN.fullmatch(token) is None:
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_NUMBER, token)
    value = float(token)
    if not math.isfinite(value) or (value == 0.0 and token.startswith("-")):
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_NUMBER, token)
    return value


def parse_integer_token(token: str, *, allow_negative_one: bool = False) -> int:
    if allow_negative_one and token == "-1":
        return -1
    if not isinstance(token, str) or INTEGER_TOKEN.fullmatch(token) is None:
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_NUMBER, token)
    return int(token)


def parse_signed_integer_token(token: str) -> int:
    if not isinstance(token, str) or SIGNED_INTEGER_TOKEN.fullmatch(token) is None or token == "-0":
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_NUMBER, token)
    return int(token)


def canonical_float(value: float) -> str:
    """Return the shortest valid QCIS decimal that round-trips to binary64."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_NUMBER, "binding is not a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_NUMBER, "binding is non-finite")
    if parsed == 0.0:
        return "0"
    token = repr(parsed)
    if "e" not in token and "E" not in token:
        return token[:-2] if token.endswith(".0") else token
    mantissa, exponent = token.lower().split("e")
    if mantissa.endswith(".0"):
        mantissa = mantissa[:-2]
    exponent_value = int(exponent)
    return f"{mantissa}e{exponent_value}"


def parse_canonical_float(token: str) -> float:
    return parse_real_token(token)


def parse_canonical_integer(token: str, *, allow_minus_one: bool = False) -> int:
    return parse_integer_token(token, allow_negative_one=allow_minus_one)


def tokenize_source(source: str) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(line.split(" ")) for line in source[:-1].split("\n"))


def validate_canonical_source(source: str, *, allow_placeholders: bool) -> None:
    if not isinstance(source, str) or not source.endswith("\n") or source.startswith("\n"):
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_SOURCE, "source must use a final LF")
    if "\r" in source or "\t" in source or "\ufeff" in source:
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_SOURCE, "source has forbidden whitespace")
    try:
        source.encode("ascii")
    except UnicodeEncodeError as exc:
        raise QCISCompilationError(QCISReasonCode.NONCANONICAL_SOURCE, "source is not ASCII") from exc
    for tokens in tokenize_source(source):
        if not tokens or any(not token for token in tokens) or not OPERATION_TOKEN.fullmatch(tokens[0]):
            raise QCISCompilationError(QCISReasonCode.NONCANONICAL_SOURCE, "invalid source line")
        for token in tokens[1:]:
            if token.startswith("$"):
                if not allow_placeholders or PLACEHOLDER_TOKEN.fullmatch(token) is None:
                    raise QCISCompilationError(QCISReasonCode.NONCANONICAL_SOURCE, f"invalid placeholder {token!r}")
