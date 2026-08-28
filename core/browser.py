"""
Browser lifecycle + authenticated course opening.

Facts this module relies on (verified 2026-08-18 from real DOM dumps):
  * /launch?... first renders on www.netacad.com, THEN redirects to Keycloak (auth.netacad.com),
    which may bounce through Google SSO (accounts.google.com) even for a persisted session.
    => "authenticated" == app host AND course outline actually rendered. Never trust the host alone.
  * The course outline renders `button[id^="node-button-"]` per course-level node.
  * Progress percentages ([data-percentage]) load asynchronously after the outline.
"""
from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from seleniumbase import SB

from .config import ROOT

log = logging.getLogger(__name__)

OUTLINE_NODE_SEL = 'button[id^="node-button-"]'


class AuthTimeout(RuntimeError):
    pass


def wait_until(sb, predicate: Callable, timeout: float, poll: float = 0.4, what: str = "condition") -> bool:
    """Poll a predicate; swallow transient driver errors during navigation. True if satisfied."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if predicate(sb):
                return True
        except Exception:
            pass
        time.sleep(poll)
    log.debug("timeout after %.0fs waiting for %s", timeout, what)
    return False


def wait_stable(sb, measure: Callable, timeout: float, ticks: int = 3, poll: float = 0.5, min_value=None) -> object:
    """Wait until a measured value stops changing for `ticks` polls (bounded). Returns last value."""
    last, stable, end = object(), 0, time.time() + timeout
    while time.time() < end and stable < ticks:
        cur = measure(sb)
        ok = cur == last and (min_value is None or cur >= min_value)
        stable = stable + 1 if ok else 0
        last = cur
        time.sleep(poll)
    return last


def current_host(sb) -> str:
    return re.sub(r"^https?://([^/]+).*$", r"\1", sb.get_current_url())


@contextmanager
def launch(cfg: dict):
    """Start Chrome. session_mode:
         ephemeral  (default) - a throw-away temp profile per run: always shows the login, never auto-picks a
                                remembered account, and is deleted when the run ends (nothing persisted).
         persistent           - the project profile dir (config browser.user_data_dir / --profile NAME): login
                                is remembered between runs on this machine (opt-in convenience)."""
    import shutil
    import tempfile
    b = cfg["browser"]
    ephemeral = b.get("session_mode", "ephemeral") != "persistent"
    if ephemeral:
        profile_dir = tempfile.mkdtemp(prefix="netacad_session_")
        log.info("Session mode: ephemeral (fresh login, nothing saved) - temp profile %s", profile_dir)
    else:
        profile_dir = str((ROOT / b["user_data_dir"]).resolve())
        log.info("Session mode: persistent - profile %s", profile_dir)
    try:
        with _sb_session(b, profile_dir) as sb:
            yield sb
    finally:
        if ephemeral:
            try:
                shutil.rmtree(profile_dir, ignore_errors=True)
                log.info("Ephemeral session removed (%s)", profile_dir)
            except Exception as e:  # noqa: BLE001
                log.warning("could not remove temp profile %s: %s", profile_dir, e)


@contextmanager
def _sb_session(b: dict, profile_dir: str):
    with SB(
        uc=b.get("uc", False),
        headed=b.get("headed", True),
        incognito=b.get("incognito", False),
        user_data_dir=profile_dir,
        window_size=b.get("window_size"),
        page_load_strategy="normal",
        # Let video.play() work without a trusted user gesture (the player lives 5 shadow roots deep,
        # so we drive it through its own HTMLMediaElement API instead of synthesising clicks).
        chromium_arg="--autoplay-policy=no-user-gesture-required",
    ) as sb:
        yield sb


def open_course(sb, cfg: dict) -> None:
    """Open the course URL and block until the authenticated course outline is rendered.

    If a real login page is showing, prompt the human to log in (credentials are never handled here).
    Raises AuthTimeout if the outline never appears.
    """
    auth, t = cfg["auth"], cfg["timeouts"]
    if auth.get("fresh_login"):
        # Clear every cookie (NetAcad, Keycloak, Google) so the next sign-in shows the login / account chooser,
        # instead of Google silently reusing the identity remembered in this profile.
        try:
            sb.driver.execute_cdp_cmd("Network.clearBrowserCookies", {})
            sb.driver.execute_cdp_cmd("Network.clearBrowserCache", {})
            log.info("fresh_login: cleared all cookies/cache in profile %s", cfg["browser"]["user_data_dir"])
        except Exception as e:  # noqa: BLE001
            log.warning("fresh_login: could not clear cookies: %s", e)
        auth["fresh_login"] = False   # one-shot: a recovery re-open must NOT log the user out again
    log.info("Opening course: %s", cfg["course"]["url"])
    # NOTE: in UC mode `sb.open()` force-activates SeleniumBase "CDP Mode" (chromedriver disconnected,
    # execute_script becomes a bare CDP expression, no `return`/`arguments`, different iframe API).
    # We want the standard WebDriver API for the whole project, so navigate via the driver directly.
    sb.driver.get(cfg["course"]["url"])

    def app_rendered(s) -> bool:
        return current_host(s) == auth["app_host"] and bool(
            s.execute_script(f"return document.querySelectorAll('{OUTLINE_NODE_SEL}').length > 0"))

    def login_form_visible(s) -> bool:
        if "accounts.google.com" in s.get_current_url():
            return True
        return bool(s.execute_script("return !!document.querySelector('#kc-form-login')"))

    started, deadline, prompted = time.time(), time.time() + auth["manual_login_timeout_sec"], False
    while not app_rendered(sb):
        if not prompted and (login_form_visible(sb) or time.time() - started > 45):
            log.warning("Waiting for authentication. If the Chrome window shows a login page, sign in there "
                        "(Google SSO / 2-Step Verification are fine). If it is just redirecting, no action needed. "
                        "Waiting up to %ss.", auth["manual_login_timeout_sec"])
            prompted = True
        if time.time() > deadline:
            raise AuthTimeout(f"course outline did not render (last host: {current_host(sb)})")
        time.sleep(1.0)
    if prompted:
        log.info("Authentication completed.")

    # Progress bars arrive asynchronously; wait until their count stabilises (bounded).
    n = wait_stable(sb, lambda s: s.execute_script("return document.querySelectorAll('[data-percentage]').length"),
                    timeout=t["element"], ticks=4, min_value=1)
    log.info("Course outline rendered; %s progress bars loaded", n)


def save_diagnostics(sb, cfg: dict, tag: str) -> Path:
    """Screenshot + HTML + URL for error analysis. Returns base path (without suffix)."""
    from datetime import datetime
    safe_tag = re.sub(r"[^A-Za-z0-9_-]+", "_", tag)  # '3.1' would otherwise be eaten as a suffix
    base = ROOT / cfg["paths"]["logs"] / f"{datetime.now():%Y-%m-%d_%H%M%S}_{safe_tag}"
    try:
        sb.save_screenshot(f"{base}.png")
        Path(f"{base}.html").write_text(sb.get_page_source(), encoding="utf-8")
        Path(f"{base}.url.txt").write_text(sb.get_current_url(), encoding="utf-8")
    except Exception as e:  # diagnostics must never mask the original error
        log.error("could not save diagnostics: %s", e)
    log.error("Diagnostics saved: %s.{png,html,url.txt}", base)
    return base
