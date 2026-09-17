"""Small deterministic helpers shared by collectors, storage and rules."""

from __future__ import annotations

import hashlib
import json
import math
import time
from decimal import Decimal, InvalidOperation
from typing import Any


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal value: {value!r}")
    return result


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, default=json_default, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def finite_float(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite numeric value: {value!r}")
    return result
