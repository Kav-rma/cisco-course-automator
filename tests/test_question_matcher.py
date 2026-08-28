import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.matcher import MATCH_NOT_CONFIDENT, QuestionBank, normalize, pick_correct_on_screen

BANK = QuestionBank([
    {"question_id": "3.1.4-q1", "lesson_id": "3.1.4",
     "question": "Which of the following is a low-power, shorter range wireless technology that is intended to replace wired connectivity for accessories such as speakers or a mouse?",
     "options": [{"text": "NFC"}, {"text": "GPS"}, {"text": "Wi-Fi"}, {"text": "Bluetooth"}], "correct_texts": ["Bluetooth"]},
    {"question_id": "3.1.4-q2", "lesson_id": "3.1.4",
     "question": "Which of the following is a wireless communication technology that enables a smartphone to communicate with a payment system within a few centimeters away?",
     "options": [{"text": "NFC"}, {"text": "GPS"}, {"text": "Wi-Fi"}, {"text": "Bluetooth"}], "correct_texts": ["NFC"]},
    {"question_id": "9.9.9-q1", "lesson_id": "9.9.9", "question": "What does DHCP stand for?",
     "options": [{"text": "A"}, {"text": "B"}], "correct_texts": ["A"]},
])


def test_normalize():
    assert normalize("  Wi-Fi,  (802.11)!’ ") == "wi fi 802 11"


def test_exact_match_order_independent():
    m = BANK.match("Which of the following is a wireless communication technology that enables a smartphone to communicate with a payment system within a few centimeters away?",
                   ["Wi-Fi", "Bluetooth", "GPS", "NFC"])
    assert m.matched and m.status == "EXACT" and m.question_id == "3.1.4-q2"


def test_fuzzy_match_small_diff():
    q = "Which of the following is a low-power, short range wireless technology intended to replace wired connectivity for accessories such as speakers or a mouse?"
    m = BANK.match(q, ["GPS", "Bluetooth", "Wi-Fi", "NFC"])
    assert m.matched and m.status == "FUZZY" and m.question_id == "3.1.4-q1" and m.confidence > 0.95


def test_not_confident_for_unknown():
    m = BANK.match("Which layer of the OSI model handles routing?", ["Network", "Transport"])
    assert not m.matched and m.status == MATCH_NOT_CONFIDENT


def test_options_mismatch_blocks_match():
    m = BANK.match("What does DHCP stand for?", ["Dynamic Host", "Static Host"])
    assert not m.matched


def test_pick_correct_on_shuffled_screen():
    shown = [{"index": 1, "text": "GPS"}, {"index": 3, "text": "Bluetooth"}, {"index": 2, "text": "Wi-Fi"}, {"index": 0, "text": "NFC"}]
    chosen = pick_correct_on_screen(BANK.entries[0], shown)
    assert [o["index"] for o in chosen] == [3]
    assert pick_correct_on_screen({"correct_texts": ["Zigbee"]}, shown) is None
