from __future__ import annotations

import re
from typing import Any


Q_PREFIX_NUMERIC_RE = re.compile(r"^Q([0-9]{6})$")


def normalize_symbol_value(symbol: Any) -> str:
    if symbol is None:
        return ""
    text = str(symbol).strip().upper()
    if not text:
        return ""

    q_match = Q_PREFIX_NUMERIC_RE.fullmatch(text)
    if q_match:
        return q_match.group(1)

    if text.isdigit():
        return text.zfill(6)

    return text


def canonical_symbol_key(symbol: Any) -> str:
    return normalize_symbol_value(symbol)


def is_q_prefixed_numeric_symbol(symbol: Any) -> bool:
    if symbol is None:
        return False
    return bool(Q_PREFIX_NUMERIC_RE.fullmatch(str(symbol).strip().upper()))
