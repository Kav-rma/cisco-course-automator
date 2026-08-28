"""
Phase 1 - Browser proof of concept / reconnaissance vehicle.

What it does:
  1. Opens Chrome (headed, persistent project profile so the login survives restarts).
  2. Opens the NetAcad course URL.
  3. If redirected to the Keycloak login (auth.netacad.com), waits for YOU to log in
     manually in the browser window (no credentials are ever handled by this script).
  4. Waits for the authenticated course application to render.
  5. Dumps reconnaissance artifacts to data/recon/ (HTML, screenshot, DOM summary JSON)
     so stable selectors can be chosen from real evidence instead of guesses.
  6. Prints the course title and visible module names it can find.

Run (from the project root):
    .venv\\Scripts\\python.exe scripts\\phase1_poc.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from seleniumbase import SB

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8-sig"))
# Optional override for smoke tests: NETACAD_LOGIN_TIMEOUT=25
if os.environ.get("NETACAD_LOGIN_TIMEOUT"):
    CONFIG["auth"]["manual_login_timeout_sec"] = int(os.environ["NETACAD_LOGIN_TIMEOUT"])

RECON_DIR = ROOT / CONFIG["paths"]["recon"]
RECON_DIR.mkdir(parents=True, exist_ok=True)


def wait_until(sb, predicate, timeout: float, poll: float = 0.5, what: str = "condition") -> bool:
    """Poll a predicate instead of sleeping blindly. Returns True if it became truthy."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if predicate(sb):
                return True
        except Exception:  # transient JS/driver errors during navigation
            pass
        time.sleep(poll)
    print(f"[WARN] Timed out after {timeout}s waiting for: {what}")
    return False


def current_host(sb) -> str:
    return re.sub(r"^https?://([^/]+).*$", r"\1", sb.get_current_url())


# JS executed in the page: collect a structural summary of the DOM without assuming any selector.
DOM_SUMMARY_JS = r"""
const q = (s) => Array.from(document.querySelectorAll(s));
const txt = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
const attrs = (el) => Object.fromEntries(Array.from(el.attributes).map(a => [a.name, a.value.slice(0, 120)]));
const summarize = (els, n = 60) => els.slice(0, n).map(el => ({tag: el.tagName.toLowerCase(), text: txt(el).slice(0, 120), attrs: attrs(el)}));

const bodyText = document.body ? document.body.innerText : '';
const lines = bodyText.split('\n').map(s => s.trim()).filter(Boolean);
const moduleLines = lines.filter(l => /^(Module\s+\d+|\d+(\.\d+)*\s+\S)/i.test(l) || /Introduction|Checkpoint|Exam|Summary/i.test(l)).slice(0, 200);

const dataAttrNames = new Set();
q('*').forEach(el => { for (const a of el.attributes) if (a.name.startsWith('data-')) dataAttrNames.add(a.name); });

const roots = q('#root, #app, [data-reactroot], #__next, main').map(el => ({tag: el.tagName.toLowerCase(), id: el.id, cls: el.className && el.className.toString().slice(0,100)}));

({
  url: location.href,
  title: document.title,
  h1: q('h1').map(txt),
  h2: q('h2').map(txt).slice(0, 40),
  iframes: q('iframe').map(f => ({src: f.src, id: f.id, name: f.name, title: f.title})),
  roots,
  counts: {
    nav: q('nav').length, aside: q('aside').length, main: q('main').length,
    roleTree: q('[role="tree"]').length, roleTreeItem: q('[role="treeitem"]').length,
    roleNavigation: q('[role="navigation"]').length, roleButton: q('[role="button"]').length,
    button: q('button').length, a: q('a').length, details: q('details').length,
    ariaExpanded: q('[aria-expanded]').length, progressbar: q('[role="progressbar"], progress').length,
    dataTestId: q('[data-testid]').length, dataCy: q('[data-cy]').length,
    shadowHosts: q('*').filter(e => e.shadowRoot).length,
  },
  dataAttrNames: Array.from(dataAttrNames).sort(),
  navs: summarize(q('nav, aside, [role="navigation"], [role="tree"]'), 20),
  ariaExpanded: summarize(q('[aria-expanded]'), 80),
  testIds: summarize(q('[data-testid]'), 120),
  progress: summarize(q('[role="progressbar"], progress, [aria-valuenow]'), 40),
  moduleLines,
  bodyTextHead: bodyText.slice(0, 4000),
})
"""


def dump_recon(sb, tag: str) -> dict:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = RECON_DIR / f"{stamp}_{tag}"
    summary = sb.execute_script(DOM_SUMMARY_JS)
    (base.with_suffix(".summary.json")).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (base.with_suffix(".html")).write_text(sb.get_page_source(), encoding="utf-8")
    sb.save_screenshot(str(base.with_suffix(".png")))
    print(f"[INFO] Recon saved: {base}.{{summary.json,html,png}}")
    return summary


def main() -> int:
    b = CONFIG["browser"]
    auth = CONFIG["auth"]
    t = CONFIG["timeouts"]
    profile_dir = str((ROOT / b["user_data_dir"]).resolve())

    with SB(
        uc=b.get("uc", False),
        headed=b.get("headed", True),
        incognito=b.get("incognito", False),
        user_data_dir=profile_dir,
        window_size=b.get("window_size"),
        page_load_strategy="normal",
    ) as sb:
        print(f"[INFO] Opening course: {CONFIG['course']['url']}")
        # sb.open() in UC mode switches to CDP Mode (non-WebDriver JS semantics); use the driver directly.
        sb.driver.get(CONFIG["course"]["url"])

        # State machine, not host-sniffing: the /launch URL sits on www.netacad.com for a moment
        # BEFORE redirecting to Keycloak, so "authenticated" must mean
        #   app host  AND  the course application has actually rendered course content.
        # Anything else (Keycloak email form, Google SSO, 2-Step Verification, ...) means
        # "keep waiting for the human to finish logging in".
        def app_rendered(s) -> bool:
            if current_host(s) != auth["app_host"]:
                return False
            return bool(s.execute_script(
                "return !!document.body && document.body.innerText.length > 500 "
                "&& /Module\\s*\\d/i.test(document.body.innerText)"))

        deadline = time.time() + auth["manual_login_timeout_sec"]
        started = time.time()
        prompted = False
        def login_form_visible(s) -> bool:
            # Keycloak email form, or any Google accounts page (SSO / 2-Step Verification).
            if "accounts.google.com" in s.get_current_url():
                return True
            return bool(s.execute_script("return !!document.querySelector('#kc-form-login')"))

        while not app_rendered(sb):
            host = current_host(sb)
            # A persisted Keycloak session bounces through auth.netacad.com automatically; only ask
            # the human to log in when a real login form is showing (or after a long grace period).
            if not prompted and (login_form_visible(sb) or time.time() - started > 45):
                print("=" * 70)
                print("[ACTION MAY BE REQUIRED] Waiting for authentication. If the Chrome window shows a")
                print("                  login page, please sign in there (Google SSO / 2-Step Verification are")
                print("                  fine). If it is just redirecting on its own, no action is needed.")
                print(f"                  Waiting up to {auth['manual_login_timeout_sec']}s ...")
                print("=" * 70)
                prompted = True
            if time.time() > deadline:
                print(f"[ERROR] Course app did not render within the timeout (last host: {host}).")
                dump_recon(sb, "timeout")
                return 2
            time.sleep(1.0)

        if prompted:
            print("[INFO] Login detected; the persistent profile should keep you signed in next run.")

        # Give the SPA a short, condition-based settle: wait until visible text stops growing.
        def text_len(s):
            return s.execute_script("return document.body.innerText.length")
        last = -1
        stable_ticks = 0
        end = time.time() + t["app_render"]
        while time.time() < end and stable_ticks < 3:
            cur = text_len(sb)
            stable_ticks = stable_ticks + 1 if cur == last else 0
            last = cur
            time.sleep(0.7)

        # Progress percentages ([data-percentage]) are loaded asynchronously after the outline renders;
        # wait until their count stops changing (bounded), rather than sleeping a fixed time.
        def pct_count(s):
            return s.execute_script("return document.querySelectorAll('[data-percentage]').length")
        last = -1
        stable_ticks = 0
        end = time.time() + t["element"]
        while time.time() < end and stable_ticks < 4:
            cur = pct_count(sb)
            stable_ticks = stable_ticks + 1 if (cur == last and cur > 0) else 0
            last = cur
            time.sleep(0.5)
        print(f"[INFO] Progress bars loaded: {last}")

        summary = dump_recon(sb, "course_landing")

        # Selectors below were derived from the saved recon HTML (data/recon/*.html), not guessed:
        #   course title : main h1
        #   course nodes : button[id^="node-button-<uuid>"]  (modules AND course-level items like exams)
        #                    label   -> [class*="nodeName--"]
        #                    progress-> [data-percentage]  (absent when the node has no progress bar)
        #                    sections-> aria-controls="node-<uuid>" -> #node-<uuid> button[id^="submodule-button-"]
        # CSS-module class hashes (nodeName--AZrtx) can change on redeploy, so we match on the
        # stable prefix with [class*="nodeName--"] rather than the full hashed class.
        outline = sb.execute_script(r"""
            const title = (document.querySelector('main h1') || {}).innerText || document.title;
            const nodes = Array.from(document.querySelectorAll('button[id^="node-button-"]')).map(btn => {
                const uuid = btn.id.replace('node-button-', '');
                const name = (btn.querySelector('[class*="nodeName--"]') || btn).innerText.trim();
                const prog = btn.querySelector('[data-percentage]');
                // NOTE: aria-controls="node-<uuid>" points at an id that does NOT exist in the DOM;
                // sections are rendered in the sibling [class*="subSection--"] inside the node container.
                const panel = btn.closest('[class*="nodeContainer--"]');
                const sections = panel ? Array.from(panel.querySelectorAll('button[id^="submodule-button-"]')).map(sb => ({
                    name: (sb.querySelector('[class*="subModuleName--"]') || {}).title || sb.innerText.trim(),
                    progress: (sb.querySelector('[class*="descendantProgress--"]') || {}).innerText || null,
                    status: (sb.querySelector('img[alt]') || {}).alt || null,
                })) : [];
                return {uuid, name, percentage: prog ? Number(prog.dataset.percentage) : null,
                        expanded: btn.getAttribute('aria-expanded') === 'true', sectionCount: sections.length, sections};
            });
            return JSON.stringify({title, nodes});
        """)
        outline = json.loads(outline)
        (RECON_DIR / "outline_phase1.json").write_text(json.dumps(outline, indent=2, ensure_ascii=False), encoding="utf-8")

        print("\nCOURSE:", outline["title"])
        print("MODULES:")
        n = 0
        for node in outline["nodes"]:
            m = re.match(r"^Module\s+(\d+):\s*(.+)$", node["name"])
            if m:
                n += 1
                pct = f"  [{node['percentage']}%]" if node["percentage"] is not None else ""
                print(f"{m.group(1):>3}. {m.group(2)}{pct}  ({node['sectionCount']} sections)")
            else:
                print(f"     * {node['name']}  (course-level item, {node['sectionCount']} sections)")
        print(f"\n[INFO] {n} modules, {len(outline['nodes'])} course-level nodes total. Outline saved to data/recon/outline_phase1.json")
        print("[INFO] Content iframe:", [f["src"][:110] for f in summary["iframes"] if f.get("title") == "Course content"])

        # Keep the browser open briefly so the user can look, but do not block forever.
        print("\n[INFO] Done. Browser closes in 30s (Ctrl+C to close now).")
        time.sleep(30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
