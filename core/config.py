"""Configuration loading. Single source of truth: config/config.json (BOM-tolerant)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.json"


COURSES_PATH = ROOT / "config" / "courses.json"


def list_courses() -> list[dict]:
    if COURSES_PATH.exists():
        return json.loads(COURSES_PATH.read_text(encoding="utf-8-sig")).get("courses", [])
    c = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")).get("course", {})
    return [{"key": "default", "name": c.get("name", "course"), "url": c.get("url")}]


def choose_course(key: str | None = None, interactive: bool = True) -> dict:
    """Pick a course from config/courses.json: by key, by env NETACAD_COURSE, the only one, or an interactive menu."""
    courses = list_courses()
    key = key or os.environ.get("NETACAD_COURSE")
    if key:
        for c in courses:
            if c["key"] == key:
                return c
        raise SystemExit(f"unknown course key '{key}'. Known: {[c['key'] for c in courses]}")
    if len(courses) == 1 or not interactive or not sys.stdin or not sys.stdin.isatty():
        return courses[0]
    print("\nWhich course do you want to run?")
    for i, c in enumerate(courses, 1):
        print(f"  {i}) {c['name']}   [{c['key']}]")
    while True:
        ans = input(f"Enter 1-{len(courses)} (or the key): ").strip()
        if ans.isdigit() and 1 <= int(ans) <= len(courses):
            return courses[int(ans) - 1]
        for c in courses:
            if c["key"] == ans:
                return c
        print("  not a valid choice")


def load_config(course: str | None = None, interactive: bool = True) -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit("config/config.json not found. Copy config/config.example.json to config/config.json "
                         "(and config/courses.example.json to config/courses.json with your course URL) first.")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    sel = choose_course(course, interactive)
    cfg["course"] = {"key": sel["key"], "name": sel["name"], "url": sel["url"]}
    # per-course data directory (structure, question bank, run reports, kc results); logs/recon stay shared
    cfg["paths"]["data"] = f"data/{sel['key']}"
    if os.environ.get("NETACAD_LOGIN_TIMEOUT"):
        cfg["auth"]["manual_login_timeout_sec"] = int(os.environ["NETACAD_LOGIN_TIMEOUT"])
    # Account switching: NETACAD_PROFILE=name -> separate Chrome profile dir "profile_<name>" (one per identity);
    # NETACAD_FRESH_LOGIN=1 -> clear all cookies in that profile before opening the course (forces the login/account chooser).
    if os.environ.get("NETACAD_PROFILE"):
        cfg["browser"]["user_data_dir"] = f"profile_{os.environ['NETACAD_PROFILE']}"
        cfg["browser"]["session_mode"] = "persistent"
    if os.environ.get("NETACAD_SESSION_MODE") in ("ephemeral", "persistent"):
        cfg["browser"]["session_mode"] = os.environ["NETACAD_SESSION_MODE"]
    if os.environ.get("NETACAD_FRESH_LOGIN") in ("1", "true", "yes"):
        cfg["auth"]["fresh_login"] = True
    for key in ("data", "logs", "screenshots", "recon"):
        (ROOT / cfg["paths"][key]).mkdir(parents=True, exist_ok=True)
    return cfg


def path(cfg: dict, key: str) -> Path:
    return ROOT / cfg["paths"][key]
