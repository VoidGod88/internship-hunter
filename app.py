"""
app.py — Gradio Web UI for WIE Internship Hunter v4.
Single-page interface: config panel + run + stop + review cover letters + send/skip.
Pipeline runs in subprocess for isolated stop/kill support.
"""
import os as _os
_os.environ["PYTHONWARNINGS"] = "ignore"

import warnings as _warnings
_warnings.filterwarnings("ignore")

import sys
import time
import json
import re
import threading
import logging
import subprocess
import yaml
from pathlib import Path

# Silence noisy third-party loggers
for _noisy in ["gradio", "gradio_client", "starlette", "uvicorn", "httpx", "urllib3"]:
    logging.getLogger(_noisy).setLevel(logging.ERROR)

# Ensure we can import from the package
sys.path.insert(0, str(Path(__file__).parent))

import gradio as gr

from config import config as cfg
from database import get_all_jobs, get_jobs_with_cover_letters, get_application_history, insert_cover_letter, get_cover_letter
from models import Job
from ai_writer import generate_cover_letter
from mailer import send_email

# ── Config auto-generation (safeguard against missing files) ──
_ENV_TEMPLATE = """# Email credentials (for sending applications)
EMAIL=
EMAIL_PASSWORD=

# LLM provider for cover letter generation
LLM_PROVIDER=deepseek
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# PolyU Jobboard (optional, leave blank if not using)
POLYU_NET_ID=
POLYU_PASSWORD=
"""

_CONFIG_TEMPLATE = """# WIE Internship Hunter v4 — User Configuration
# All values can also be edited in the web UI (⚙️ Config tab)

cv_pdf_path: ""

search_keywords:
  - "summer internship 2026 computer science"
  - "software engineer intern summer 2026"
  - "AI internship summer 2026"
  - "data science intern summer 2026"

scrapers:
  polyu: true
  linkedin: true
  jobsdb: true
  indeed: true
  efc: true
  manual: true

wie:
  require_hk_location: true
  require_cs_role: true
  exclude_final_year: true
  exclude_phd: true

cv_match:
  enabled: true
  skills_weight: 0.4
  education_weight: 0.3
  experience_weight: 0.3

cover_letter:
  enabled: true
  language: "en"

email:
  subject_template: "Application for {job_title} – Summer Internship"
  attach_cv: true
  dry_run: true
  max_emails_per_run: 10
  send_delay_seconds: 5
"""


def _ensure_config_files():
    """Create .env and config.yaml from templates if they don't exist.
    This protects users who clone the repo — gitignored files won't be present.
    """
    project_dir = Path(__file__).parent
    env_path = project_dir / ".env"
    cfg_path = project_dir / "config.yaml"

    if not env_path.exists():
        env_path.write_text(_ENV_TEMPLATE, encoding="utf-8")
        print(f"[Setup] Created default .env at {env_path}")

    if not cfg_path.exists():
        cfg_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        print(f"[Setup] Created default config.yaml at {cfg_path}")


log = logging.getLogger("hunter")
logging.basicConfig(level=logging.INFO)

# ── Subprocess State ──
_pipeline_proc = None          # subprocess.Popen
_pipeline_lock = threading.Lock()
_pipeline_running = False
STATUS_FILE = str(Path(__file__).parent / ".pipeline_status.json")
PYTHON_EXE = sys.executable
HUNTER_SCRIPT = str(Path(__file__).parent / "hunter.py")

# Log tail thread: prints subprocess hunter.log to this terminal
_log_tail_thread = None
_log_tail_stop = threading.Event()


def _start_log_tail():
    """Start background thread to tail hunter.log to terminal."""
    global _log_tail_thread, _log_tail_stop
    _log_tail_stop.clear()
    log_path = str(Path(__file__).parent / "hunter.log")
    # Clear log on new run for cleaner output
    try:
        if _os.path.exists(log_path):
            _os.remove(log_path)
    except Exception:
        pass
    _log_tail_thread = threading.Thread(target=_tail_log_worker, args=(log_path,), daemon=True)
    _log_tail_thread.start()


def _stop_log_tail():
    """Stop the log tail thread."""
    _log_tail_stop.set()


def _tail_log_worker(log_path: str):
    """Tail hunter.log and print new lines to terminal."""
    last_pos = 0
    # Wait for log file to be created
    for _ in range(20):
        if _os.path.exists(log_path):
            break
        if _log_tail_stop.is_set():
            return
        time.sleep(0.5)
    while not _log_tail_stop.is_set():
        try:
            if _os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(last_pos)
                    lines = f.readlines()
                    if lines:
                        for line in lines:
                            line = line.rstrip()
                            if line:
                                print(f"[hunter] {line}", flush=True)
                        last_pos = f.tell()
        except Exception:
            pass
        # Check every 0.3s
        _log_tail_stop.wait(timeout=0.3)


def _read_status():
    """Read pipeline status from the JSON file."""
    try:
        if _os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, encoding="utf-8", errors="ignore") as f:
                return json.loads(f.read())
    except Exception as e:
        log.warning(f"[Status] Read error: {e}")
    return {"status": "idle", "phase": "idle", "message": ""}


def _write_status(data):
    """Write pipeline status JSON."""
    try:
        _os.makedirs(_os.path.dirname(STATUS_FILE), exist_ok=True)
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
    except Exception as e:
        log.warning(f"[Status] Write error: {e}")


def _run_pipeline(keywords_str, dry_run, max_emails, scraper_polyu, scraper_linkedin,
                  scraper_jobsdb, scraper_indeed, scraper_efc, scraper_manual,
                  cover_letter_enabled):
    """Launch pipeline in a subprocess. Returns initial status."""
    global _pipeline_proc, _pipeline_running

    with _pipeline_lock:
        if _pipeline_running and _pipeline_proc and _pipeline_proc.poll() is None:
            return "⚠️ Pipeline already running! Stop it first.", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

        # Build CLI args
        cmd = [
            PYTHON_EXE, HUNTER_SCRIPT,
            "--status-file", STATUS_FILE,
            "--max-emails", str(int(max_emails)),
        ]
        if dry_run:
            cmd.append("--dry-run")
        if keywords_str.strip():
            cmd += ["--keywords", keywords_str]
        if scraper_polyu:
            cmd.append("--scraper-polyu")
        if scraper_linkedin:
            cmd.append("--scraper-linkedin")
        if scraper_jobsdb:
            cmd.append("--scraper-jobsdb")
        if scraper_indeed:
            cmd.append("--scraper-indeed")
        if scraper_efc:
            cmd.append("--scraper-efc")
        if scraper_manual:
            cmd.append("--scraper-manual")
        if cover_letter_enabled:
            cmd.append("--cover-letter")

        # Also update config in-process for email sending etc.
        cfg.dry_run = dry_run
        cfg.max_emails_per_run = max_emails
        cfg.scraper_polyu = scraper_polyu
        cfg.scraper_linkedin = scraper_linkedin
        cfg.scraper_jobsdb = scraper_jobsdb
        cfg.scraper_indeed = scraper_indeed
        cfg.scraper_efc = scraper_efc
        cfg.scraper_manual = scraper_manual
        cfg.cover_letter_enabled = cover_letter_enabled

        # Write initial status
        _write_status({"status": "running", "phase": "init", "message": "Starting pipeline..."})

        # Launch subprocess
        try:
            # Start log tail thread (prints hunter.log to terminal)
            _start_log_tail()

            # DEVNULL: avoid pipe buffer deadlock — subprocess communicates via STATUS_FILE
            _pipeline_proc = subprocess.Popen(
                cmd,
                cwd=str(Path(__file__).parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            _pipeline_running = True
            log.info(f"[Subprocess] Started PID={_pipeline_proc.pid}")
        except Exception as e:
            _write_status({"status": "error", "phase": "error", "message": str(e)})
            return f"❌ Failed to start: {e}", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

    msg = (
        f"▶️ Pipeline started (PID={_pipeline_proc.pid})...\n"
        f"   Keywords: {keywords_str or '(default)'}\n"
        f"   Scrapers: {'PolyU' if scraper_polyu else ''}{' LinkedIn' if scraper_linkedin else ''}"
        f"{' JobsDB' if scraper_jobsdb else ''}{' Indeed' if scraper_indeed else ''}"
        f"{' eFC' if scraper_efc else ''}{' Manual' if scraper_manual else ''}\n"
        f"   Dry run: {dry_run} | Max emails: {max_emails}"
    )
    return msg, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()


def _stop_pipeline():
    """Kill the running pipeline subprocess."""
    global _pipeline_proc, _pipeline_running
    with _pipeline_lock:
        if not _pipeline_running or not _pipeline_proc:
            return "⚠️ No pipeline running.", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()

        pid = _pipeline_proc.pid
        try:
            _pipeline_proc.terminate()
            time.sleep(1)
            if _pipeline_proc.poll() is None:
                _pipeline_proc.kill()
            _pipeline_running = False
            _write_status({"status": "stopped", "phase": "stopped", "message": "Pipeline stopped by user."})
            log.info(f"[Subprocess] Killed PID={pid}")
            msg = f"⏹️ Stopped! (killed PID={pid})"
        except Exception as e:
            msg = f"❌ Could not stop: {e}"
        _pipeline_proc = None
        _pipeline_running = False
        # Stop log tail thread
        _stop_log_tail()

    # Refresh data in case some jobs were saved before stop
    jobs_data = _get_jobs_dataframe()
    cl_data = _get_cover_letter_list()
    history_data = _get_history_dataframe()
    log_text = _get_log_content()
    return msg, jobs_data, cl_data, history_data, gr.update(), log_text


def _poll_status():
    """Poll pipeline status file and update UI. Called by Gradio timer."""
    # Must declare global before any reference (Python requirement)
    global _pipeline_proc, _pipeline_running

    status = _read_status()
    phase = status.get("phase", "idle")

    msg = status.get("message", "")

    # Check if subprocess ended
    if _pipeline_running and _pipeline_proc and _pipeline_proc.poll() is not None:
        _pipeline_running = False
        rc = _pipeline_proc.returncode
        if rc == 0 and status.get("status") == "done":
            msg = "✅ " + status.get("message", "Done!")
            phase = "done"
        elif status.get("status") == "stopped":
            msg = "⏹ " + status.get("message", "Stopped.")
            phase = "stopped"
        else:
            msg = f"❌ Pipeline failed (exit={rc})"
            phase = "error"
        _pipeline_proc = None
        _stop_log_tail()
    else:
        # Still running or idle: show last 5 log lines in status output
        log_path = str(Path(__file__).parent / "hunter.log")
        recent = []
        try:
            if _os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                for line in lines:
                    line = line.rstrip()
                    if not line:
                        continue
                    # Strip timestamp + level: "2026-06-16 21:12:48,638  INFO     Message"
                    m = re.match(
                        r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+\s+\w+\s+(.+)$',
                        line
                    )
                    if m:
                        recent.append(m.group(1))
                    else:
                        recent.append(line[-120:])
                recent = recent[-5:]
        except Exception:
            pass
        if recent:
            msg = "\n".join(recent)

    jobs_data = _get_jobs_dataframe()
    history_data = _get_history_dataframe()
    log_text = _get_log_content()

    progress_html = _build_progress_bar(status, phase)

    return msg, jobs_data, history_data, progress_html, log_text


def _build_progress_bar(status: dict, phase: str) -> str:
    """Build an HTML progress bar showing pipeline phase."""
    phases = ["init", "scraping", "processing", "generating", "done", "stopped", "error"]
    labels = {"init": "Init", "scraping": "Scraping", "processing": "Processing",
              "generating": "Generating", "done": "Done", "stopped": "Stopped", "error": "Error"}
    colors = {"init": "#6b7280", "scraping": "#3b82f6", "processing": "#f59e0b",
              "generating": "#8b5cf6", "done": "#10b981", "stopped": "#ef4444", "error": "#ef4444"}

    if phase == "idle":
        return '<div style="color:#6b7280;font-size:13px;padding:4px 0">Ready to start...</div>'

    try:
        idx = phases.index(phase)
    except ValueError:
        idx = 0

    pct = int((idx / (len(phases) - 3)) * 100) if idx < len(phases) - 3 else 100
    color = colors.get(phase, "#6b7280")
    label = labels.get(phase, phase)
    msg = status.get("message", "")[:80]

    return f'''<div style="width:100%;background:#374151;border-radius:6px;height:18px;overflow:hidden;margin:4px 0">
        <div style="width:{pct}%;height:100%;background:{color};border-radius:6px;transition:width 0.4s"></div>
    </div>
    <div style="font-size:12px;color:#9ca3af">[{label}] {msg}</div>'''


def _get_log_content(max_lines=50):
    """Read the last N lines of hunter.log for display."""
    log_path = str(Path(__file__).parent / "hunter.log")
    try:
        if not _os.path.exists(log_path):
            return "(No log yet)"
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        # Get last N lines, skip empty
        recent = [l.rstrip() for l in lines[-max_lines:] if l.strip()]
        return "\n".join(recent) if recent else "(Log empty)"
    except Exception as e:
        return f"(Log read error: {e})"


def _get_jobs_dataframe():
    """Get all jobs from DB as a 2D list for gradio dataframe."""
    jobs = get_all_jobs()
    if not jobs:
        return []
    rows = []
    for j in jobs:
        rows.append([
            j.get("id", ""),
            j.get("company", ""),
            j.get("title", "")[:60],
            j.get("source", ""),
            j.get("url", "")[:80],
            "✅" if j.get("wie_eligible") else "❌",
            j.get("wie_reason", ""),
            j.get("extra_docs", "") or "-",
            j.get("status", "New"),
            j.get("contact_email", ""),
        ])
    return rows


def _get_cover_letter_list():
    """Get jobs that have cover letters, for the dropdown."""
    rows = get_jobs_with_cover_letters()
    if not rows:
        return gr.update(choices=[], value=None)
    choices = [f"[{r['id']}] {r['company']} - {r['title'][:50]}" for r in rows]
    return gr.update(choices=choices, value=choices[0] if choices else None)


def _load_cl_for_job(selected_job_label: str):
    """Auto-load cover letter when job selection changes. Read-only, no generation."""
    if not selected_job_label:
        return "", ""
    try:
        job_id = int(selected_job_label.split("]")[0].replace("[", ""))
    except (ValueError, IndexError):
        return "Invalid selection", ""

    existing = get_cover_letter(job_id)
    if existing:
        return existing, existing
    else:
        return "No cover letter in DB. Click 'Generate' to create one.", ""


def _get_history_dataframe():
    """Get application history as 2D list for gradio dataframe."""
    history = get_application_history()
    if not history:
        return []
    rows = []
    for h in history:
        rows.append([
            h.get("sent_at", ""),
            h.get("company", ""),
            h.get("title", "")[:60],
            "🔶 Dry Run" if h.get("dry_run") else "✅ Sent",
        ])
    return rows


def _generate_cl_for_job(selected_job_label: str):
    """Generate cover letter for a specific job. Checks DB first, saves after generation."""
    if not selected_job_label:
        return "Please select a job first", ""
    try:
        job_id = int(selected_job_label.split("]")[0].replace("[", ""))
    except (ValueError, IndexError):
        return "Invalid job selection", ""

    # Check if cover letter already exists in DB
    existing = get_cover_letter(job_id)
    if existing:
        return existing, existing

    jobs = get_all_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return "Job not found", ""

    cover_letter = generate_cover_letter(
        job["title"], job["company"], job.get("description", ""),
        job.get("requirements", ""), job.get("education_level", "")
    )

    # Store in DB for future retrieval
    try:
        insert_cover_letter(job_id, cover_letter)
    except Exception:
        pass

    return cover_letter, cover_letter


def _send_single_email(selected_job_label: str, cover_letter_edit: str, dry_run: bool):
    """Send or dry-run a single email."""
    if not selected_job_label:
        return "Please select a job first"
    try:
        job_id = int(selected_job_label.split("]")[0].replace("[", ""))
    except (ValueError, IndexError):
        return "Invalid job selection"

    jobs = get_all_jobs()
    j = next((job for job in jobs if job["id"] == job_id), None)
    if not j:
        return "Job not found"

    job = Job(
        title=j["title"], company=j["company"],
        contact_email=j.get("contact_email", ""),
    )

    success = send_email(job, cover_letter_edit or "", dry_run=dry_run)
    if success:
        return f"{'🔶 [DRY RUN]' if dry_run else '✅ SENT'} — {j['company']} - {j['title'][:50]}"
    else:
        return f"❌ Failed: {j['company']} (no contact email or credentials)"


def _refresh_all():
    """Refresh all data views."""
    jobs_data = _get_jobs_dataframe()
    cl_update = _get_cover_letter_list()
    hist_data = _get_history_dataframe()
    return jobs_data, cl_update, hist_data


def _initial_load():
    """Initial data load — called on page load."""
    try:
        return _refresh_all()
    except Exception as e:
        log.exception("Initial load failed")
        return [], gr.update(choices=[]), []


# ─────────────────────────────────────────────
# Config Tab: read/write .env + config.yaml from UI
# ─────────────────────────────────────────────

def _read_env_file():
    """Parse .env file into dict, preserving non-key line positions."""
    env_path = Path(__file__).parent / ".env"
    result = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def _write_env_file(updates):
    """Write updated .env file, preserving non-key lines. `updates` = dict of key→value."""
    env_path = Path(__file__).parent / ".env"
    updated_keys = set(updates.keys())
    new_lines = []
    key_positions = {}

    if env_path.exists():
        for i, line in enumerate(env_path.read_text(encoding="utf-8").splitlines()):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                key_positions[key] = i
                if key in updated_keys:
                    new_lines.append(f"{key}={updates[key]}")
                    continue
            new_lines.append(line)
    else:
        new_lines = []

    # Append any new keys not found in file
    for k, v in updates.items():
        if k not in key_positions:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _write_yaml_config(config_dict):
    """Write config.yaml using yaml.dump (clean, no comments)."""
    yaml_path = Path(__file__).parent / "config.yaml"

    data = {
        "cv_pdf_path": config_dict.get("cv_pdf_path", ""),
        "dry_run": config_dict.get("dry_run", True),
        "max_emails_per_run": int(config_dict.get("max_emails_per_run", 10)),
        "search_keywords": [
            k.strip() for k in str(config_dict.get("search_keywords", "")).split(",") if k.strip()
        ],
        "scrapers": {
            "polyu_jobboard": bool(config_dict.get("scraper_polyu", True)),
            "linkedin": bool(config_dict.get("scraper_linkedin", True)),
            "jobsdb": bool(config_dict.get("scraper_jobsdb", True)),
            "indeed": bool(config_dict.get("scraper_indeed", True)),
            "efinancialcareers": bool(config_dict.get("scraper_efc", True)),
            "manual_companies": bool(config_dict.get("scraper_manual", True)),
        },
        "wie_filter": {
            "enabled": bool(config_dict.get("wie_enabled", True)),
            "require_hk_location": bool(config_dict.get("wie_require_hk", True)),
            "exclude_non_cs": bool(config_dict.get("wie_exclude_non_cs", True)),
            "exclude_final_year_required": bool(config_dict.get("wie_exclude_final_year", True)),
        },
        "cv_matching": {
            "enabled": bool(config_dict.get("cv_matching_enabled", True)),
            "match_education": bool(config_dict.get("cv_match_education", True)),
            "match_skills": bool(config_dict.get("cv_match_skills", True)),
            "match_final_year": bool(config_dict.get("cv_match_final_year", True)),
        },
        "cover_letter": {
            "enabled": bool(config_dict.get("cover_letter_enabled", True)),
            "language": str(config_dict.get("cover_letter_language", "en")),
        },
        "email_settings": {
            "subject_template": str(config_dict.get("email_subject_template",
                "Summer 2026 Internship Application – {title} | {name} (PolyU CS)")),
            "attach_cv": bool(config_dict.get("email_attach_cv", True)),
            "delay_seconds": int(config_dict.get("email_delay_seconds", 5)),
        },
    }

    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _on_cv_upload(cv_file):
    """Handle CV PDF upload: copy to project dir and return new path."""
    if not cv_file:
        return gr.update()
    try:
        import shutil
        project_dir = Path(__file__).parent
        src = Path(cv_file)
        # Use original filename, sanitized
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', src.name)
        dst = project_dir / safe_name
        if not dst.exists() or src.resolve() != dst.resolve():
            shutil.copy2(str(src), str(dst))
        return str(dst.resolve())
    except Exception as e:
        log.exception("CV upload failed")
        return gr.update(value=f"❌ Upload failed: {e}")


def _save_config_from_ui(
    email, email_password, llm_provider, llm_api_key, llm_base_url, llm_model,
    polyu_net_id, polyu_password,
    cv_pdf_path, dry_run, max_emails, search_keywords,
    scraper_polyu, scraper_linkedin, scraper_jobsdb, scraper_indeed, scraper_efc, scraper_manual,
    wie_enabled, wie_require_hk, wie_exclude_non_cs, wie_exclude_final_year,
    cv_matching_enabled, cv_match_education, cv_match_skills, cv_match_final_year,
    cover_letter_enabled, cover_letter_language, email_subject, email_attach_cv, email_delay,
):
    """Save all config to .env + config.yaml, reload in-process."""
    try:
        # 1. Write .env (secrets)
        _write_env_file({
            "EMAIL": email or "",
            "EMAIL_PASSWORD": email_password or "",
            "LLM_PROVIDER": llm_provider or "deepseek",
            "LLM_API_KEY": llm_api_key or "",
            "LLM_BASE_URL": llm_base_url or "https://api.deepseek.com",
            "LLM_MODEL": llm_model or "deepseek-chat",
            "POLYU_NET_ID": polyu_net_id or "",
            "POLYU_PASSWORD": polyu_password or "",
        })

        # 2. Write config.yaml (settings)
        _write_yaml_config({
            "cv_pdf_path": cv_pdf_path or "",
            "dry_run": dry_run,
            "max_emails_per_run": int(max_emails),
            "search_keywords": search_keywords or "",
            "scraper_polyu": scraper_polyu,
            "scraper_linkedin": scraper_linkedin,
            "scraper_jobsdb": scraper_jobsdb,
            "scraper_indeed": scraper_indeed,
            "scraper_efc": scraper_efc,
            "scraper_manual": scraper_manual,
            "wie_enabled": wie_enabled,
            "wie_require_hk": wie_require_hk,
            "wie_exclude_non_cs": wie_exclude_non_cs,
            "wie_exclude_final_year": wie_exclude_final_year,
            "cv_matching_enabled": cv_matching_enabled,
            "cv_match_education": cv_match_education,
            "cv_match_skills": cv_match_skills,
            "cv_match_final_year": cv_match_final_year,
            "cover_letter_enabled": cover_letter_enabled,
            "cover_letter_language": cover_letter_language or "en",
            "email_subject_template": email_subject or "",
            "email_attach_cv": email_attach_cv,
            "email_delay_seconds": int(email_delay),
        })

        # 3. Reload config in-memory
        cfg.reload_inplace()

        return "✅ Config saved & reloaded successfully!"
    except Exception as e:
        log.exception("Save config failed")
        return f"❌ Save failed: {e}"


# ─────────────────────────────────────────────
# Gradio UI
# ─────────────────────────────────────────────

with gr.Blocks(title="WIE Internship Hunter v4") as app:
    gr.Markdown(
        "# 🎯 WIE Internship Hunter v4\n"
        "PolyU Jobboard + LinkedIn + JobsDB + Indeed + eFinancialCareers → AI Cover Letters → Email"
    )

    with gr.Row():
        # ── LEFT COLUMN: Controls ──
        with gr.Column(scale=1, min_width=340):
            gr.Markdown("### ⚙️ Configuration")

            with gr.Group():
                keywords_input = gr.Textbox(
                    label="Search Keywords (comma-separated)",
                    value=", ".join(cfg.search_keywords),
                    lines=3,
                )
                with gr.Row():
                    dry_run_toggle = gr.Checkbox(label="🧪 Dry Run", value=cfg.dry_run)
                    cover_letter_toggle = gr.Checkbox(label="🤖 AI Cover Letter", value=cfg.cover_letter_enabled)
                max_emails_slider = gr.Slider(
                    label="Max Emails per Run", minimum=1, maximum=50,
                    value=cfg.max_emails_per_run, step=1,
                )

            with gr.Group():
                gr.Markdown("**Scrapers**")
                with gr.Row():
                    scraper_polyu = gr.Checkbox(label="PolyU Jobboard", value=cfg.scraper_polyu)
                    scraper_linkedin = gr.Checkbox(label="LinkedIn", value=cfg.scraper_linkedin)
                with gr.Row():
                    scraper_jobsdb = gr.Checkbox(label="JobsDB", value=cfg.scraper_jobsdb)
                    scraper_indeed = gr.Checkbox(label="Indeed", value=cfg.scraper_indeed)
                with gr.Row():
                    scraper_efc = gr.Checkbox(label="eFinancialCareers", value=cfg.scraper_efc)
                    scraper_manual = gr.Checkbox(label="Manual Companies", value=cfg.scraper_manual)

            run_btn = gr.Button("▶️  RUN PIPELINE", variant="primary", size="lg")
            stop_btn = gr.Button("⏹ Stop Pipeline", variant="stop", size="sm", visible=True)
            refresh_btn = gr.Button("🔄 Refresh Data", size="sm")

            progress_bar = gr.HTML(value='<div style="color:#6b7280;font-size:13px;padding:4px 0">Ready to start...</div>')

            status_output = gr.Textbox(
                label="Status", lines=6, interactive=False,
                value="Ready. Configure and click Run.",
            )

            gr.Markdown("---")
            gr.Markdown("### 📧 Manual Send")
            job_selector = gr.Dropdown(
                label="Select Job", choices=[], interactive=True,
            )
            send_info = gr.Textbox(label="Send Info", interactive=False, value="Select a job and generate cover letter first.")

        # ── RIGHT COLUMN: Results ──
        with gr.Column(scale=2):
            with gr.Tabs():
                with gr.Tab("📋 Jobs"):
                    jobs_table = gr.Dataframe(
                        headers=["ID", "Company", "Title", "Source", "Link", "WIE", "Reason", "Req'd Docs", "Status", "Email"],
                        interactive=False,
                    )

                with gr.Tab("✉️ Cover Letter"):
                    with gr.Row():
                        cl_job_selector = gr.Dropdown(
                            label="Select Job for Cover Letter",
                            choices=[],
                            interactive=True,
                        )
                        generate_cl_btn = gr.Button("🤖 Generate Cover Letter", variant="secondary")
                    cl_output = gr.Textbox(
                        label="Cover Letter (editable)",
                        lines=18,
                        interactive=True,
                    )
                    with gr.Row():
                        send_cl_btn = gr.Button("📤 Send (Real)", variant="primary")
                        dry_send_cl_btn = gr.Button("🔶 Dry Run Send", variant="secondary")
                    cl_status = gr.Textbox(label="Send Status", interactive=False)

                with gr.Tab("📊 History"):
                    history_table = gr.Dataframe(
                        headers=["Date", "Company", "Title", "Dry Run"],
                        interactive=False,
                    )

                with gr.Tab("📝 Live Log"):
                    log_output = gr.Textbox(
                        label="hunter.log (auto-refresh every 2s)",
                        lines=20,
                        interactive=False,
                        autoscroll=True,
                    )

                with gr.Tab("📖 Help"):
                    gr.Markdown("""
                    ## WIE Internship Hunter v4 — User Guide

                    ### Quick Start
                    1. **Setup credentials**: Go to the ⚙️ Config tab
                    2. **Set keywords & scrapers**: Left panel or Config tab
                    3. **Install dependencies**: `pip install -r requirements.txt`
                    4. **Install Playwright**: `python -m playwright install chromium`
                    5. **Launch UI**: `python app.py`
                    6. **Open browser**: http://localhost:7861

                    ### Workflow
                    1. Configure keywords, scrapers, and settings in the left panel
                    2. Click **RUN PIPELINE** to scrape jobs
                    3. Switch to **Cover Letter** tab to review AI-generated letters
                    4. Edit the cover letter if needed, then click:
                       - **Dry Run Send** — preview only, no real email
                       - **Send (Real)** — actually sends the email via Gmail
                    5. Check the **History** tab to see what was sent

                    ### Tips
                    - Start with Dry Run enabled to test
                    - PolyU Jobboard requires NetID in the Config tab
                    - AI cover letters require LLM API key in the Config tab (DeepSeek/OpenAI)
                    - Max emails per run prevents accidental mass sending
                    - Cookies are saved so you don't need to re-login to PolyU
                    """)

                with gr.Tab("⚙️ Config"):
                    gr.Markdown("### All settings are saved to `.env` + `config.yaml` automatically")

                    # ── Section 1: Credentials ──
                    gr.Markdown("#### 🔐 Credentials (saved to `.env`)")
                    with gr.Row():
                        config_email = gr.Textbox(label="Email (Gmail)", value=cfg.email or "")
                        config_email_pw = gr.Textbox(label="Email Password (App Password)", value=cfg.email_password or "", type="password")
                    with gr.Row():
                        config_llm_key = gr.Textbox(label="LLM API Key", value=cfg.llm_api_key or "", type="password")
                        config_llm_provider = gr.Dropdown(label="LLM Provider", choices=["deepseek", "openai", "custom"], value=cfg.llm_provider)
                    with gr.Row():
                        config_llm_base = gr.Textbox(label="LLM Base URL", value=cfg.llm_base_url)
                        config_llm_model = gr.Textbox(label="LLM Model", value=cfg.llm_model)
                    with gr.Row():
                        config_polyu_id = gr.Textbox(label="PolyU NetID", value=cfg.polyu_net_id or "")
                        config_polyu_pw = gr.Textbox(label="PolyU Password", value=cfg.polyu_password or "", type="password")

                    gr.Markdown("---")

                    # ── Section 2: CV & File ──
                    gr.Markdown("#### 📄 CV & Files")
                    with gr.Row():
                        config_cv_path = gr.Textbox(label="CV PDF Path", value=cfg.cv_pdf_path or "", scale=3)
                        config_cv_upload = gr.File(label="Upload CV PDF", file_types=[".pdf"], type="filepath", scale=1)

                    # ── Section 3: Search ──
                    gr.Markdown("#### 🔍 Search & Limits")
                    config_keywords = gr.Textbox(label="Search Keywords (comma-separated)", value=", ".join(cfg.search_keywords), lines=3)
                    with gr.Row():
                        config_dry_run = gr.Checkbox(label="🧪 Dry Run (default)", value=cfg.dry_run)
                        config_max_emails = gr.Slider(label="Max Emails per Run", minimum=1, maximum=50, value=cfg.max_emails_per_run, step=1)

                    # ── Section 4: Scrapers ──
                    gr.Markdown("#### 🕷️ Scrapers")
                    with gr.Row():
                        config_scraper_polyu = gr.Checkbox(label="PolyU Jobboard", value=cfg.scraper_polyu)
                        config_scraper_linkedin = gr.Checkbox(label="LinkedIn", value=cfg.scraper_linkedin)
                        config_scraper_jobsdb = gr.Checkbox(label="JobsDB", value=cfg.scraper_jobsdb)
                    with gr.Row():
                        config_scraper_indeed = gr.Checkbox(label="Indeed", value=cfg.scraper_indeed)
                        config_scraper_efc = gr.Checkbox(label="eFinancialCareers", value=cfg.scraper_efc)
                        config_scraper_manual = gr.Checkbox(label="Manual Companies", value=cfg.scraper_manual)

                    # ── Section 5: WIE Filter ──
                    gr.Markdown("#### 🎓 WIE Filter")
                    with gr.Row():
                        config_wie_enabled = gr.Checkbox(label="WIE Filter Enabled", value=cfg.wie_enabled)
                        config_wie_hk = gr.Checkbox(label="Require HK Location", value=cfg.wie_require_hk)
                    with gr.Row():
                        config_wie_non_cs = gr.Checkbox(label="Exclude Non-CS Roles", value=cfg.wie_exclude_non_cs)
                        config_wie_final_yr = gr.Checkbox(label="Exclude Final-Year-Only", value=cfg.wie_exclude_final_year)

                    # ── Section 6: CV Matching ──
                    gr.Markdown("#### 🤖 CV Matching")
                    with gr.Row():
                        config_cv_match_enabled = gr.Checkbox(label="CV Matching Enabled", value=cfg.cv_matching_enabled)
                        config_cv_match_edu = gr.Checkbox(label="Match Education", value=cfg.cv_match_education)
                    with gr.Row():
                        config_cv_match_skills = gr.Checkbox(label="Match Skills", value=cfg.cv_match_skills)
                        config_cv_match_fy = gr.Checkbox(label="Match Final Year", value=cfg.cv_match_final_year)

                    # ── Section 7: Cover Letter ──
                    gr.Markdown("#### ✉️ Cover Letter")
                    with gr.Row():
                        config_cl_enabled = gr.Checkbox(label="AI Cover Letter Enabled", value=cfg.cover_letter_enabled)
                        config_cl_lang = gr.Dropdown(label="Language", choices=["en", "zh"], value=cfg.cover_letter_language)

                    # ── Section 8: Email ──
                    gr.Markdown("#### 📧 Email Settings")
                    config_email_subj = gr.Textbox(label="Subject Template", value=cfg.email_subject_template)
                    with gr.Row():
                        config_email_attach = gr.Checkbox(label="Attach CV PDF", value=cfg.email_attach_cv)
                        config_email_delay = gr.Slider(label="Delay Between Emails (sec)", minimum=1, maximum=30, value=cfg.email_delay_seconds, step=1)

                    # ── Save Button ──
                    gr.Markdown("---")
                    with gr.Row():
                        config_save_btn = gr.Button("💾 Save Configuration", variant="primary", size="lg")
                        config_save_status = gr.Textbox(label="Save Status", interactive=False)

    # ── Event handlers ──

    run_btn.click(
        fn=_run_pipeline,
        inputs=[
            keywords_input, dry_run_toggle, max_emails_slider,
            scraper_polyu, scraper_linkedin, scraper_jobsdb, scraper_indeed,
            scraper_efc, scraper_manual, cover_letter_toggle,
        ],
        outputs=[status_output, jobs_table, cl_job_selector, history_table, progress_bar, log_output],
    )

    stop_btn.click(
        fn=_stop_pipeline,
        inputs=[],
        outputs=[status_output, jobs_table, cl_job_selector, history_table, progress_bar, log_output],
    )

    refresh_btn.click(
        fn=_refresh_all,
        inputs=[],
        outputs=[jobs_table, cl_job_selector, history_table],
    )

    # Auto-polling timer: updates status while pipeline is running
    poll_timer = gr.Timer(value=2)
    poll_timer.tick(
        fn=_poll_status,
        inputs=[],
        outputs=[status_output, jobs_table, history_table, progress_bar, log_output],
    )

    # CV upload: auto-fill path after upload
    config_cv_upload.change(
        fn=_on_cv_upload,
        inputs=[config_cv_upload],
        outputs=[config_cv_path],
    )

    generate_cl_btn.click(
        fn=_generate_cl_for_job,
        inputs=[cl_job_selector],
        outputs=[cl_output, cl_status],
    )

    cl_job_selector.change(
        fn=_load_cl_for_job,
        inputs=[cl_job_selector],
        outputs=[cl_output, cl_status],
    )

    send_cl_btn.click(
        fn=lambda job, cl: _send_single_email(job, cl, dry_run=False),
        inputs=[cl_job_selector, cl_output],
        outputs=[cl_status],
    )

    dry_send_cl_btn.click(
        fn=lambda job, cl: _send_single_email(job, cl, dry_run=True),
        inputs=[cl_job_selector, cl_output],
        outputs=[cl_status],
    )

    config_save_btn.click(
        fn=_save_config_from_ui,
        inputs=[
            config_email, config_email_pw, config_llm_provider, config_llm_key,
            config_llm_base, config_llm_model, config_polyu_id, config_polyu_pw,
            config_cv_path, config_dry_run, config_max_emails, config_keywords,
            config_scraper_polyu, config_scraper_linkedin, config_scraper_jobsdb,
            config_scraper_indeed, config_scraper_efc, config_scraper_manual,
            config_wie_enabled, config_wie_hk, config_wie_non_cs, config_wie_final_yr,
            config_cv_match_enabled, config_cv_match_edu, config_cv_match_skills, config_cv_match_fy,
            config_cl_enabled, config_cl_lang, config_email_subj, config_email_attach, config_email_delay,
        ],
        outputs=[config_save_status],
    )

    # Initial data load
    app.load(
        fn=_initial_load,
        inputs=[],
        outputs=[jobs_table, cl_job_selector, history_table],
    )

    app.queue(default_concurrency_limit=5)


if __name__ == "__main__":
    import socket
    import subprocess

    # --- Auto-kill previous instance on the same port ---
    PORT = 7861
    def _free_port(port):
        """Kill any process occupying the target port (Windows)."""
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    print(f"Found existing Gradio process PID={pid} on port {port}, killing...")
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
                    print("Previous instance killed.")
                    break
        except Exception:
            pass

    _free_port(PORT)

    # ---- Auto-generate config files if missing ----
    _ensure_config_files()

    print("=" * 60)
    print("WIE Internship Hunter v4 — Gradio Web UI")
    print(f"Open http://localhost:{PORT} in your browser")
    print("=" * 60)
    app.launch(
        server_name="0.0.0.0",
        server_port=PORT,
        share=False,
        inbrowser=True,
    )
