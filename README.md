# NetAcad Course Automator

Local Python + [SeleniumBase](https://seleniumbase.io/) automation for Cisco NetAcad courses. It automates the
repetitive, **non-graded** parts of a course — reading lessons, playing videos, clicking through interactives,
completing ungraded knowledge checks — and gives you a **study-assist workflow** for graded quizzes and
checkpoint exams where **you always make and submit every answer yourself**.

> ## ⚖️ The hard boundary
> This tool **never answers or submits graded assessments**. Module quizzes, checkpoint exams, final exams and
> surveys are never auto-solved: the automation skips them, and the assist tool only *displays your own
> previously-solved answers* on screen while **you** click the options and press Submit. That line is by design
> and is not a configuration option. Automating graded work is academic dishonesty — don't ask the tool to do
> it, and don't modify it to.

Everything runs locally on your machine, in a visible Chrome window. No server, no accounts, no telemetry.

---

## How login works (read this first)

**The code never sees your credentials.** There is no password handling anywhere:

1. A run opens Chrome at your course's `/launch` URL. NetAcad redirects to its Keycloak login, which may bounce
   through Google SSO.
2. If a login page appears, **you sign in by hand in that window** (Google SSO and 2-step verification are
   fine). The script just waits — up to 10 minutes — and detects success only when the authenticated course
   outline actually renders.
3. Whether you stay logged in between runs depends on the **session mode**:

| Mode | Behavior |
|---|---|
| `ephemeral` (shipped default) | Throwaway Chrome profile per run, deleted afterwards. Every run asks you to log in; nothing is stored. |
| `persistent` | Chrome uses the project-local `profile/` folder, so cookies survive and you log in **once**. Opt in per-run with `--keep-session`, or set `browser.session_mode` to `"persistent"` in `config/config.json`. |

Extra knobs (all scripts that open the browser accept them):

- `--profile alice` — use folder `profile_alice/` instead: one folder per NetAcad/Google identity, for
  switching accounts.
- `--fresh-login` — one-shot: clears all cookies in the profile before opening the course, forcing the
  login/account chooser again.
- Env-var equivalents: `NETACAD_COURSE`, `NETACAD_PROFILE`, `NETACAD_SESSION_MODE`, `NETACAD_FRESH_LOGIN`,
  `NETACAD_LOGIN_TIMEOUT`.

> ⚠️ The `profile/` and `profile_*/` folders **are** your live login. Never share or commit them
> (they're git-ignored).

---

## Setup

Windows, Python 3.11+, Chrome installed.

```
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

copy config\config.example.json config\config.json
copy config\courses.example.json config\courses.json
```

Then edit `config/courses.json` and add your course(s): a short `key`, a display `name`, and the course's
launch `url` (open your course on netacad.com and copy the address — it looks like
`https://www.netacad.com/launch?id=...&tab=curriculum&view=...`).

Every script below asks **which course** to run when you have more than one (or pass `--course <key>`).
Each course keeps its own data under `data/<key>/`.

---

## Workflow

Run everything from the project root with `.venv\Scripts\python.exe`.

### Step 1 — Map the course (once per course)

```
.venv\Scripts\python.exe scripts\01_course_explorer.py --course my-course
```

Walks the live course outline (modules → sections → items) and writes `data/<key>/course_structure.json`.
Opens no lessons, touches no assessments. Everything else reads this map.

### Step 2 — Collect knowledge-check questions (optional but recommended)

```
.venv\Scripts\python.exe scripts\02_content_collector.py --course my-course
```

Visits the **ungraded** "Check Your Understanding" blocks and stores their questions/answers in
`data/<key>/question_bank.json`, which Step 3 uses to complete those checks. Graded quizzes/exams are skipped.

### Step 3 — Automate the non-graded content

```
.venv\Scripts\python.exe scripts\03_main.py --course my-course --keep-session
```

The main automator. Resumes at the first incomplete item and, for each one: navigates → detects the page type →
runs the matching handler → verifies the outline marks it complete. Handles lessons, videos (seeks to the end),
accordions/tabs/flip-cards/hotgraphics/animations, and ungraded knowledge checks (MCQ, matching, sorting, plus
built-in solvers for binary/decimal/IPv6/ANDing/cable-pinout practice widgets).

**Skipped and reported to you** (never forced): graded quizzes/exams/surveys, Packet Tracer labs and mini-games,
and any page it can't confidently classify. A run report lands in `data/<key>/run_report_*.json`.

Useful flags: `--start 3.1.1` (begin at an item), `--modules 3,4` (limit modules), `--max-items 20`,
`--dry-run` (detect page types only, change nothing).

### Step 4 — Study workflow for graded quizzes (you solve, you submit)

```
# 4a. extract every module-quiz question + options (read-only: answers nothing, submits nothing)
.venv\Scripts\python.exe scripts\extract_quizzes.py --course my-course

# 4b. turn the bank into a fill-in answer sheet: data/<key>/quiz_answersheet.txt
.venv\Scripts\python.exe scripts\format_quizzes.py --course my-course
```

**4c. Solve the sheet yourself.** Save your answers as `data/<key>/quiz_answers_user.json`:

```json
{ "QUIZ 1.4.3 — Module Quiz - ...": { "Q1": "C", "Q2": "A, D", "Q3": "B" } }
```

```
# 4d. join your answers with the extracted questions -> data/<key>/quiz_answer_key.json
.venv\Scripts\python.exe scripts\build_answer_key.py --course my-course
```

### Step 5 — Same thing for checkpoint exams

```
.venv\Scripts\python.exe scripts\extract_exams.py --course my-course     # writes exam_questions.json + exam_answersheet.txt
# ...solve the sheet, save as data/<key>/exam_answers_user.json ("EXAM exam-1 — ...": {"Q1": "C", ...})
.venv\Scripts\python.exe scripts\build_answer_key.py --exams --course my-course
```

Notes: extraction opens the exam and pages through it with "Skip Question" (nothing answered, nothing
submitted), abandoning the attempt — NetAcad lets you retake checkpoint exams. Exams already completed on your
account are skipped. `--fill` re-runs exams with missing questions and merges what it finds.

### Step 6 — Take the quizzes/exams with the assist panel

```
.venv\Scripts\python.exe scripts\assist_quizzes.py --keep-session
```

Fully manual by design. It opens the course and pins a green **"📘 Fetch answers"** button top-left. You
navigate to any quiz or checkpoint exam, press Start yourself, and when a question is on screen you click the
button: it recognizes which quiz/exam you're in (by matching the question text against your answer key) and
shows **your saved answers** for it in a side panel. You click the options and press Submit. The script never
selects, submits, skips, or navigates.

Panel tips: answers are listed by question number, but option letters shuffle between attempts — match by the
**text** of the option. Questions you couldn't solve from the sheet (exhibit/PT-activity ones) show
"answer this one yourself".

### Anytime — check progress

```
.venv\Scripts\python.exe scripts\check_status.py 3.1.1 3.2.4
```

Read-only: prints the live outline status of the given item ids.

---

## Project layout

```
core/       config, browser/login lifecycle, outline access, navigation,
            content-frame (shadow-DOM walker), page detection, question extraction
handlers/   per-page-type automation (lesson, video, interactive, knowledge check, activities)
scripts/    the numbered workflow + quiz/exam tools above (probe_*.py are dev experiments)
config/     config.json + courses.json (yours, git-ignored) and the *.example.json templates
data/<key>/ per-course output: structure, question banks, answer sheets/keys, run reports (git-ignored)
```

## Troubleshooting

- **Stuck at "Waiting for authentication"** — sign in inside the Chrome window; the script resumes on its own.
- **Chrome flagged the automation / SSO refuses** — `browser.uc` must stay `true` (undetected-Chrome mode).
- **Wrong Google account auto-selected** — run once with `--fresh-login`, or use a separate `--profile NAME`.
- **A page fails or is unknown** — a screenshot + HTML dump lands in `logs/`, and the item is listed in the run
  report; those items are yours to do by hand.

## Publishing / privacy checklist

`.gitignore` already excludes them, but double-check before pushing a fork: `profile*/` (live logins),
`data/` (extracted Cisco question content — **copyrighted, don't publish**, plus your personal answers),
`logs/` (screenshots of your logged-in session), and your `config/*.json`.

## Disclaimer

Not affiliated with or endorsed by Cisco. Use it only on your own account, at your own risk, in line with your
institution's academic-integrity policy and NetAcad's terms of use. The graded-assessment boundary exists so
the tool helps you *study* — the work that earns the grade stays yours.
