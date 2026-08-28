import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.number_tools import and_octets, bits_to_decimal, decimal_to_bits


def test_bits_to_decimal():
    assert bits_to_decimal(["1", "0", "1", "0", "0", "1", "1", "0"]) == 166
    assert bits_to_decimal("00000110") == 6


def test_decimal_to_bits():
    assert decimal_to_bits(6) == list("00000110")
    assert decimal_to_bits(255) == list("11111111")
    assert decimal_to_bits(0) == list("00000000")


def test_and_octets_course_example():
    # 23.1.6 example: 46 & 224 -> 32
    assert and_octets("00101110", "11100000") == "00100000"
    assert and_octets("00001010", "11111111") == "00001010"
    assert int(and_octets("00101110", "11100000"), 2) == 32
