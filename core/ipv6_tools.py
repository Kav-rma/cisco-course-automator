"""Pure helpers for the IPv6-representation activities (testable offline)."""
from __future__ import annotations


def omit_leading_zeros(hextet: str) -> str:
    h = hextet.strip().lower().lstrip("0")
    return h or "0"


def compress_ipv6(hextets: list[str]) -> str:
    """RFC 5952 canonical text: leading zeros removed, the longest run (>=2) of zero hextets replaced by '::'
    (first run wins on ties)."""
    parts = [omit_leading_zeros(h) for h in hextets]
    best_start, best_len = -1, 0
    i = 0
    while i < len(parts):
        if parts[i] == "0":
            j = i
            while j < len(parts) and parts[j] == "0":
                j += 1
            if j - i > best_len:
                best_start, best_len = i, j - i
            i = j
        else:
            i += 1
    if best_len >= 2:
        left = ":".join(parts[:best_start])
        right = ":".join(parts[best_start + best_len:])
        return f"{left}::{right}"
    return ":".join(parts)
