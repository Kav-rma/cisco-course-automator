"""Pure helpers for the binary/decimal/ANDing activities (testable offline)."""
from __future__ import annotations


def bits_to_decimal(bits: list[str] | str) -> int:
    s = "".join(bits) if isinstance(bits, list) else bits
    return int(s, 2)


def decimal_to_bits(value: int, width: int = 8) -> list[str]:
    return list(format(value, f"0{width}b"))


def and_octets(host_bin: str, mask_bin: str) -> str:
    return format(int(host_bin, 2) & int(mask_bin, 2), f"0{len(host_bin)}b")
