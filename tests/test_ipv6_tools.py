import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.ipv6_tools import compress_ipv6, omit_leading_zeros


def test_omit():
    assert omit_leading_zeros("0db8") == "db8"
    assert omit_leading_zeros("0000") == "0"
    assert omit_leading_zeros("0001") == "1"
    assert omit_leading_zeros("bb2b") == "bb2b"


def test_compress_examples_from_course():
    assert compress_ipv6(["2001", "0db8", "0000", "1234", "5678", "9101", "1112", "1113"]) == "2001:db8:0:1234:5678:9101:1112:1113"
    assert compress_ipv6(["fe80", "0000", "0000", "0000", "6678", "9101", "0000", "34ab"]) == "fe80::6678:9101:0:34ab"
    assert compress_ipv6(["2001", "0000", "0db8", "1111", "0000", "0000", "0000", "0200"]) == "2001:0:db8:1111::200"
    assert compress_ipv6(["0000"] * 7 + ["0001"]) == "::1"
    assert compress_ipv6(["fe80", "0000", "0000", "0000", "0000", "0000", "0101", "1111"]) == "fe80::101:1111"
    assert compress_ipv6(["bb2b", "ef12", "bff3", "9125", "1111", "0101", "1111", "0101"]) == "bb2b:ef12:bff3:9125:1111:101:1111:101"
    assert compress_ipv6(["2001", "0db8", "2233", "4455", "6677", "0000", "0000", "0101"]) == "2001:db8:2233:4455:6677::101"
