"""Offline tests for outline parsing helpers (no browser)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.outline import classify_node, infer_item_type, split_id_title  # noqa: E402


def test_split_section_title():
    assert split_id_title("3.1. Wireless Networks") == ("3.1", "Wireless Networks")
    assert split_id_title("6.0. Introduction") == ("6.0", "Introduction")


def test_split_item_title():
    assert split_id_title("3.1.1 Video - Types of Wireless Networks") == ("3.1.1", "Video - Types of Wireless Networks")
    assert split_id_title("3.1.4 Check Your Understanding - Wireless Networks") == ("3.1.4", "Check Your Understanding - Wireless Networks")


def test_split_without_id():
    assert split_id_title("Course Introduction") == (None, "Course Introduction")
    assert split_id_title(None) == (None, "")


def test_classify_node():
    assert classify_node("Module 3: Wireless and Mobile Networks") == ("module", 3)
    assert classify_node("Checkpoint Exam: Build a Small Network") == ("checkpoint_exam", None)
    assert classify_node("Networking Essentials: Course Final Exam") == ("final_exam", None)
    assert classify_node("End of Course Survey") == ("survey", None)
    assert classify_node("Course Introduction") == ("course_introduction", None)


def test_infer_item_type():
    assert infer_item_type("Video - Types of Wireless Networks") == "video"
    assert infer_item_type("Check Your Understanding - Wireless Networks") == "knowledge_check"
    assert infer_item_type("Other Wireless Networks") == "content"
    assert infer_item_type("Webster - Why Should I Take this Module?") == "introduction"
    assert infer_item_type("What Did I Learn in this Module?") == "summary"
    assert infer_item_type("Packet Tracer - Connect to a Web Server") == "lab"
