"""
Internship Hunter — FastAPI Web UI
- No Gradio dependency, pure FastAPI + native HTML/JS + SSE
- Incremental log push via SSE (no stale log replay)
- Dropdown job selector + large detail panel
- Logs cleared on browser tab close
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
import yaml
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import uvicorn

# ── Project imports ──
sys.path.insert(0, str(Path(__file__).parent))
from database import (  # noqa: E402
    get_all_jobs,
    get_application_history,
    insert_cover_letter,
    get_cover_letter,
    update_job_cv_match,
    update_job_description,
)
from config import config as cfg  # noqa: E402
from ai_writer import generate_cover_letter  # noqa: E402
from cv_reader import get_cv_keywords, load_cv_profile  # noqa: E402
from fetch_job_detail import fetch_job_detail  # noqa: E402
from mailer import send_email  # noqa: E402
import database as _db

# ── Paths ──
BASE_DIR = Path(__file__).parent.resolve()
HUNTER_SCRIPT = str(BASE_DIR / "hunter.py")
STATUS_FILE = str(BASE_DIR / "pipeline_status.json")
LOG_FILE = str(BASE_DIR / "hunter.log")


# ── Logging ──
import logging as _logging

log = _logging.getLogger("web_ui")
log.setLevel(_logging.INFO)
if not log.handlers:
    _h = _logging.FileHandler(LOG_FILE, encoding="utf-8")
    _h.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(_h)
    _s = _logging.StreamHandler()
    _s.setFormatter(_logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(_s)


# ── globals ──
_pipeline_proc: Optional[subprocess.Popen] = None
_pipeline_running = False
_linkedin_login_proc: Optional[subprocess.Popen] = None
_sses: List[asyncio.Queue] = []
_jobs_cache: Optional[List[dict]] = None
_history_cache: Optional[List[dict]] = None
_last_log_position = 0  # log file seek position for incremental push

JOB_DETAILS_DIR = Path(__file__).parent / "data" / "job_details"
JOB_DETAILS_DIR.mkdir(parents=True, exist_ok=True)


# ── helpers ──
def _invalidate_caches():
    global _jobs_cache, _history_cache
    _jobs_cache = None
    _history_cache = None


def _cached_jobs_df() -> List[dict]:
    global _jobs_cache
    if _jobs_cache is not None:
        return _jobs_cache
    jobs = get_all_jobs()
    result = []
    for j in jobs:
        result.append({
            "id": j.get("id"),
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "source": j.get("source", ""),
            "url": j.get("url", ""),
            "wie_eligible": bool(j.get("wie_eligible", False)),
            "status": j.get("status", ""),
            "has_cl": bool(j.get("cover_letter")),
            "cv_match": j.get("cv_match", ""),
            "description": j.get("description", ""),
            "requirements": j.get("requirements", ""),
            "education_level": j.get("education_level", ""),
            "salary": j.get("salary", ""),
            "job_type": j.get("job_type", ""),
            "posted_date": j.get("posted_date", ""),
        })
    _jobs_cache = result
    return result


def _cached_history_df() -> List[dict]:
    global _history_cache
    if _history_cache is not None:
        return _history_cache
    history = get_application_history()
    result = []
    for h in history:
        result.append({
            "company": h.get("company", ""),
            "title": h.get("title", ""),
            "sent_at": h.get("sent_at", ""),
            "dry_run": bool(h.get("dry_run", False)),
        })
    _history_cache = result
    return result


def _read_status() -> dict:
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "idle", "phase": "idle", "message": ""}


def _write_status(data: dict):
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _build_progress_bar(status: dict, phase: str) -> str:
    pct_map = {
        "init": 5, "scraping": 25, "processing": 50,
        "generating": 75, "sending": 90,
        "done": 100, "stopped": 100, "error": 100,
    }
    pct = pct_map.get(phase, 0)
    label = status.get("message", phase)
    color = "#10b981" if phase in ("done", "sending") else \
            "#ef4444" if phase == "error" else \
            "#f59e0b" if phase == "stopped" else "#2563eb"
    return (
        f'<div style="margin:8px 0">'
        f'<div style="font-size:12px;color:#64748b;margin-bottom:4px">{label}</div>'
        f'<div style="height:8px;background:#e2e8f0;border-radius:4px;overflow:hidden">'
        f'<div style="height:100%;width:{pct}%;background:{color};border-radius:4px;transition:width .3s"></div>'
        f'</div></div>'
    )


def _stop_log_tail():
    pass


def _read_new_log_lines() -> str:
    """Read only new content appended to hunter.log since last read."""
    global _last_log_position
    try:
        if not os.path.exists(LOG_FILE):
            return ""
        file_size = os.path.getsize(LOG_FILE)
        if file_size < _last_log_position:
            _last_log_position = 0
        if file_size == _last_log_position:
            return ""
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            f.seek(_last_log_position)
            new_content = f.read()
            _last_log_position = f.tell()
        return new_content
    except Exception:
        return ""


def _read_full_log() -> str:
    """Read the last N lines of log file for initial load."""
    try:
        if not os.path.exists(LOG_FILE):
            return ""
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-200:])  # last 200 lines
    except Exception:
        return ""


# ── SSE ──
async def broadcast_event(event_type: str, data: dict):
    """Push an SSE event to all connected clients."""
    dead = []
    for q in _sses:
        try:
            q.put_nowait((event_type, data))
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _sses.remove(q)
        except ValueError:
            pass


async def _poll_and_broadcast():
    """Background task: poll status every 3s, broadcast to SSE clients."""
    global _pipeline_running, _pipeline_proc
    last_status_key = ""
    while True:
        await asyncio.sleep(3)
        try:
            status = _read_status()
            phase = status.get("phase", "idle")
            msg = status.get("message", "")

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
                _invalidate_caches()
                global _last_log_position
                _last_log_position = 0  # reset so next run re-reads full log

            running = _pipeline_running
            new_log = _read_new_log_lines()

            status_key = f"{phase}:{msg}:{running}"
            has_change = (status_key != last_status_key) or new_log

            if has_change:
                jobs = list(_cached_jobs_df()) if has_change else []
                history = list(_cached_history_df()) if has_change else []
                payload = {
                    "status": status,
                    "running": running,
                    "phase": phase,
                    "message": msg,
                    "new_log": new_log,
                }
                if status_key != last_status_key:
                    payload["jobs"] = jobs
                    payload["history"] = history
                    payload["progress_html"] = _build_progress_bar(status, phase)
                    last_status_key = status_key
                await broadcast_event("status", payload)
        except Exception as e:
            log.warning(f"Poll error: {e}")


# ── FastAPI lifespan ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_poll_and_broadcast())
    # Clear log on server start (fresh session)
    try:
        log_path = BASE_DIR / "hunter.log"
        if log_path.exists():
            log_path.write_text("", encoding="utf-8")
    except Exception:
        pass
    _last_log_position = 0
    log.info("Web UI started — http://0.0.0.0:7861")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)

# ── Routes ──
@app.get("/api/sse")
async def sse_endpoint(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sses.append(q)
    full_log = _read_full_log()

    async def event_generator():
        status = _read_status()
        running = _pipeline_running
        jobs = list(_cached_jobs_df())
        history = list(_cached_history_df())
        initial = {
            "event": "status",
            "data": {
                "status": status,
                "running": running,
                "jobs": jobs,
                "history": history,
                "new_log": "",
                "full_log": full_log,
            }
        }
        yield f"data: {json.dumps(initial['data'])}\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event_type, data = await asyncio.wait_for(q.get(), timeout=30)
                yield f"data: {json.dumps(data)}\n\n"
            except asyncio.TimeoutError:
                yield ":keepalive\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/jobs")
async def api_jobs():
    jobs = get_all_jobs()
    return JSONResponse({"jobs": _cached_jobs_df(), "total": len(jobs)})


@app.get("/api/jobs/{job_id}")
async def api_job_detail(job_id: int):
    jobs = get_all_jobs()
    job = next((j for j in jobs if j.get("id") == job_id), None)
    if not job:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({k: str(v) if isinstance(v, (bool, type(None))) else v
                         for k, v in job.items()})


@app.get("/api/history")
async def api_history():
    history = get_application_history()
    return JSONResponse({"history": _cached_history_df()})


@app.get("/api/status")
async def api_status():
    status = _read_status()
    running = (_pipeline_proc is not None and _pipeline_proc.poll() is None)
    return JSONResponse({"status": status, "running": running})


@app.get("/api/cover-letter/{job_id}")
async def api_get_cl(job_id: int):
    cl = get_cover_letter(job_id)
    return JSONResponse({"job_id": job_id, "cover_letter": cl or ""})


@app.get("/api/job-detail/{job_id}")
async def api_job_detail_file(job_id: int):
    """Load structured detail saved by /api/fetch-detail."""
    p = JOB_DETAILS_DIR / f"{job_id}.json"
    if not p.exists():
        return JSONResponse({"error": "No structured detail yet"}, status_code=404)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return JSONResponse({"success": True, "structured": data})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/generate-cl/{job_id}")
async def api_generate_cl(job_id: int):
    try:
        jobs = get_all_jobs()
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        cfg.reload_inplace()
        cl = await asyncio.to_thread(
            generate_cover_letter,
            job.get("title", ""),
            job.get("company", ""),
            job.get("description", "") or "",
            job.get("requirements", "") or "",
            job.get("education_level", "") or "",
        )
        insert_cover_letter(job_id, cl)
        return JSONResponse({"job_id": job_id, "cover_letter": cl})
    except Exception as e:
        log.exception("Generate CL error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/send-email/{job_id}")
async def api_send_email(job_id: int):
    try:
        dry = await asyncio.to_thread(send_email, job_id)
        return JSONResponse({"success": True, "dry_run": dry})
    except Exception as e:
        log.exception("Send email error")
        return JSONResponse({"error": str(e)}, status_code=500)


async def _evaluate_cv_match(cv_profile: dict, job: dict) -> dict:
    """Call LLM to evaluate CV against job. Returns dict with overall_match etc."""
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=cfg.llm_api_key,
            base_url=cfg.llm_base_url or None,
        )
        cv_text = json.dumps(cv_profile, ensure_ascii=False, indent=2)[:3000]
        job_text = (
            f"Title: {job.get('title', '')}\n"
            f"Company: {job.get('company', '')}\n"
            f"Description: {job.get('description', '')[:2000]}\n"
            f"Requirements: {job.get('requirements', '')[:1000]}\n"
        )
        prompt = (
            "You are a strict CV screener. Compare the CV profile JSON and the job posting.\n"
            "Return ONLY a JSON object with these keys:\n"
            "  overall_match: bool (true if candidate is a good fit)\n"
            "  skills_match: bool\n"
            "  education_match: bool\n"
            "  major_match: bool\n"
            "  match_score: int (0-100)\n"
            "  reasons: string (short explanation in Chinese)\n"
            "  requires_final_year: bool (if job requires final-year students)\n"
            "  candidate_is_final_year: bool (set to false if unknown)\n\n"
            f"CV Profile:\n{cv_text}\n\nJob:\n{job_text}"
        )
        resp = client.chat.completions.create(
            model=cfg.llm_model or "deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        content = resp.choices[0].message.content or ""
        import re
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group())
        return {"overall_match": False, "reasons": "Failed to parse LLM response"}
    except Exception as e:
        log.warning(f"Evaluate error: {e}")
        return {"overall_match": False, "reasons": str(e)}


@app.post("/api/evaluate/{job_id}")
async def api_evaluate(job_id: int):
    try:
        jobs = get_all_jobs()
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        cv_profile = await asyncio.to_thread(load_cv_profile, cfg.cv_pdf_path, cfg)
        result = await _evaluate_cv_match(cv_profile, job)
        update_job_cv_match(job_id, json.dumps(result, ensure_ascii=False))
        details = []
        if not result.get("skills_match", True): details.append("技能不匹配")
        if not result.get("education_match", True): details.append("学历不符")
        if not result.get("major_match", True): details.append("专业不符")
        if result.get("requires_final_year", False) and not result.get("candidate_is_final_year", False):
            details.append("要求final year")
        msg = f"{'✅ Match' if result.get('overall_match') else '❌ Mismatch'} — {', '.join(details) or 'all checks passed'}"
        return JSONResponse({"success": True, "result": result, "message": msg, "overall_match": result.get("overall_match", False)})
    except Exception as e:
        log.exception("CV evaluation error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/fetch-detail/{job_id}")
async def api_fetch_detail(job_id: int):
    try:
        jobs = get_all_jobs()
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)
        url = job.get("url", "")
        if not url:
            return JSONResponse({"error": "No URL for this job"}, status_code=400)
        detail = await asyncio.to_thread(fetch_job_detail, url)
        if "error" in detail and not detail.get("description"):
            return JSONResponse({"error": detail["error"], "detail": detail})
        if detail.get("description"):
            update_job_description(job_id, detail["description"])
        # Save structured result to file for frontend display
        if detail.get("structured"):
            try:
                (JOB_DETAILS_DIR / f"{job_id}.json").write_text(
                    json.dumps(detail["structured"], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                log.warning(f"[Fetch Detail] Failed to save structured to file: {e}")
        return JSONResponse({"success": True, "detail": detail})
    except Exception as e:
        log.exception("Fetch detail error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/keywords-from-cv")
async def api_keywords_from_cv():
    try:
        cfg.reload_inplace()
        if not cfg.cv_pdf_path or not os.path.exists(cfg.cv_pdf_path):
            return JSONResponse({"error": "CV PDF not found"}, status_code=400)
        keywords = await asyncio.to_thread(get_cv_keywords, cfg.cv_pdf_path, cfg, force_reload=False)
        joined = ", ".join(keywords.get("technical", []) + keywords.get("domains", []) + keywords.get("roles", []))
        return JSONResponse({"keywords": joined, "raw": keywords})
    except Exception as e:
        log.exception("Keywords from CV error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run")
async def api_run(
    keywords: str = Form(""),
    fresh: bool = Form(False),
    scraper_polyu: bool = Form(False),
    scraper_linkedin: bool = Form(False),
    scraper_jobsdb: bool = Form(False),
    scraper_indeed: bool = Form(False),
    scraper_efc: bool = Form(False),
    scraper_manual: bool = Form(False),
):
    global _pipeline_proc, _pipeline_running
    if _pipeline_running:
        return JSONResponse({"error": "Pipeline already running"}, status_code=409)

    # Fresh run: delete DB + status BEFORE launching hunter.py
    if fresh:
        _db_path = BASE_DIR / "hunter.db"
        _status_path = Path(STATUS_FILE)
        _data_dir = BASE_DIR / "data"
        for _f in [_db_path, _status_path]:
            if _f.exists():
                _f.unlink()
                log.info(f"[Fresh] Deleted {_f}")
        if _data_dir.exists():
            import shutil
            shutil.rmtree(_data_dir)
            log.info(f"[Fresh] Deleted {_data_dir}")

    cmd = [sys.executable, HUNTER_SCRIPT, "--status-file", STATUS_FILE]
    if fresh:
        cmd.append("--fresh")
    if keywords.strip():
        cmd += ["--keywords", keywords.strip()]
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

    _write_status({"status": "running", "phase": "init", "message": "Starting..."})
    global _last_log_position
    _last_log_position = 0  # reset log position on new pipeline
    try:
        _pipeline_proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        _pipeline_running = True
        log.info(f"Pipeline started PID={_pipeline_proc.pid}")
        return JSONResponse({"success": True, "pid": _pipeline_proc.pid})
    except Exception as e:
        log.exception("Failed to start pipeline")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/stop")
async def api_stop():
    global _pipeline_proc, _pipeline_running
    if not _pipeline_running or not _pipeline_proc:
        return JSONResponse({"error": "No pipeline running"}, status_code=400)
    try:
        _pipeline_proc.terminate()
        try:
            _pipeline_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _pipeline_proc.kill()
            _pipeline_proc.wait(timeout=3)
    except Exception as e:
        log.warning(f"Stop error: {e}")
    _pipeline_running = False
    _pipeline_proc = None
    _write_status({"status": "stopped", "phase": "stopped", "message": "Stopped by user"})
    return JSONResponse({"success": True})


@app.post("/api/settings")
async def api_save_settings(data: dict):
    try:
        raw = data.get("config_yaml", "")
        env_text = data.get("env", "")
        if raw:
            yaml_path = BASE_DIR / "config.yaml"
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(raw)
        if env_text:
            env_path = BASE_DIR / ".env"
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(env_text)
        cfg.reload_inplace()
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/config")
async def api_get_config():
    env_text = ""
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        env_text = env_path.read_text(encoding="utf-8")
    yaml_text = ""
    yaml_path = BASE_DIR / "config.yaml"
    if yaml_path.exists():
        yaml_text = yaml_path.read_text(encoding="utf-8")
    # Check if critical config is missing
    config_warnings = []
    if not cfg.email or "your_email" in cfg.email:
        config_warnings.append("Email not configured")
    if not cfg.llm_api_key or "your_api_key" in cfg.llm_api_key:
        config_warnings.append("LLM API key not configured")
    if not cfg.cv_pdf_path or "path/to" in cfg.cv_pdf_path:
        config_warnings.append("CV PDF path not set")
    return JSONResponse({"env": env_text, "config_yaml": yaml_text, "warnings": config_warnings})


@app.post("/api/clear-log")
async def api_clear_log():
    """Clear hunter.log (called on browser tab close)."""
    try:
        log_path = BASE_DIR / "hunter.log"
        log_path.write_text("", encoding="utf-8")
        global _log_file_position
        _log_file_position = 0
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/linkedin-login")
async def api_linkedin_login():
    """Launch linkedin_login.py as a subprocess (opens a headed browser for manual login)."""
    global _linkedin_login_proc
    # If already running, don't launch again
    if _linkedin_login_proc and _linkedin_login_proc.poll() is None:
        return JSONResponse({"success": True, "status": "already_running"})
    # Reset so previous finished process doesn't block re-launch
    _linkedin_login_proc = None
    login_script = str(BASE_DIR / "linkedin_login.py")
    try:
        # Must NOT use CREATE_NO_WINDOW — the login script needs to open a visible browser
        # Also don't suppress stdout/stderr — user needs to see login progress in run.bat console
        _linkedin_login_proc = subprocess.Popen(
            [sys.executable, login_script],
            cwd=str(BASE_DIR),
        )
        log.info(f"LinkedIn login script started PID={_linkedin_login_proc.pid}")
        return JSONResponse({"success": True, "pid": _linkedin_login_proc.pid})
    except Exception as e:
        log.exception("Failed to start LinkedIn login script")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/cl/update/{job_id}")
async def api_update_cl(job_id: int, content: str = Form("")):
    try:
        insert_cover_letter(job_id, content)
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── HTML Page ──
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Internship Hunter</title>
<style>
:root {
  --bg: #f1f5f9; --card: #fff; --border: #e2e8f0;
  --text: #1e293b; --muted: #64748b; --accent: #2563eb;
  --green: #10b981; --red: #ef4444; --orange: #f59e0b;
  --purple: #8b5cf6; --radius: 8px; --shadow: 0 1px 3px rgba(0,0,0,.08);
}
* { box-sizing:border-box; margin:0; padding:0; }
body { font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  background:var(--bg); color:var(--text); min-height:100vh; }

/* Layout */
.app { max-width:1400px; margin:0 auto; padding:12px 16px; display:flex; flex-direction:column; min-height:100vh; }
.header { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid var(--border); margin-bottom:12px; }
.header h1 { font-size:18px; font-weight:700; }
.status-dot { width:10px; height:10px; border-radius:50%; display:inline-block; flex-shrink:0; }
.status-dot.idle { background:#94a3b8; }
.status-dot.running { background:var(--green); animation:pulse 1s infinite; }
.status-dot.error { background:var(--red); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
#headerStatus { font-size:13px; color:var(--muted); }

/* Slim top bar: only job selector */
.top-bar-slim { display:flex; gap:12px; align-items:center; margin-bottom:12px; }
.top-bar-slim select { min-width:320px; padding:6px 10px; border:1px solid var(--border); border-radius:6px; font-size:13px; background:var(--card); cursor:pointer; }

/* Main area: detail panel */
.main-area { display:flex; gap:12px; flex:1; min-height:0; margin-bottom:12px; }
.detail-panel { flex:1; background:var(--card); border-radius:var(--radius); border:1px solid var(--border);
  padding:16px; overflow-y:auto; display:flex; flex-direction:column; min-height:350px; }
.detail-panel h2 { font-size:16px; margin-bottom:4px; }
.detail-meta { display:flex; gap:8px; flex-wrap:wrap; margin:6px 0 12px; font-size:13px; color:var(--muted); }
.detail-section { margin-bottom:14px; }
.detail-section h3 { font-size:13px; color:var(--muted); margin-bottom:6px; text-transform:uppercase; letter-spacing:.5px; }
.detail-text { font-size:13px; line-height:1.7; white-space:pre-wrap; word-break:break-word;
  border:1px solid var(--border); border-radius:6px; padding:12px; background:#f8fafc; max-height:300px; overflow-y:auto; }
.badge { display:inline-block; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:500; }
.badge-green { background:#d1fae5; color:#065f46; }
.badge-red { background:#fee2e2; color:#991b1b; }
.badge-gray { background:#f1f5f9; color:#475569; }
.badge-blue { background:#dbeafe; color:#1e40af; }

/* Action buttons row */
.action-row { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }

/* Control panel: between detail and log */
.control-panel { background:var(--card); border-radius:var(--radius); border:1px solid var(--border);
  padding:12px 16px; display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap; margin-bottom:12px; }
.control-group { display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap; }
.input-sm { padding:10px 14px; border:1px solid var(--border); border-radius:8px; font-size:15px; background:var(--card); }
.input-sm:focus { outline:none; border-color:var(--accent); }
select.input-sm { min-width:200px; cursor:pointer; }
.check-tags { display:flex; gap:4px; flex-wrap:wrap; }
.check-tag { font-size:12px; padding:3px 8px; border:1px solid var(--border); border-radius:12px; cursor:pointer; user-select:none; display:flex; align-items:center; gap:4px; background:var(--card); transition:.15s; }
.check-tag:hover { border-color:var(--accent); }
.check-tag input { accent-color:var(--accent); }
.btn { display:inline-flex; align-items:center; gap:6px; padding:10px 20px;
  border:none; border-radius:8px; font-size:15px; font-weight:500; cursor:pointer; transition:.15s; white-space:nowrap; }
.btn:disabled { opacity:.5; cursor:not-allowed; }
.btn-primary { background:var(--accent); color:#fff; }
.btn-green { background:var(--green); color:#fff; }
.btn-red { background:var(--red); color:#fff; }
.btn-orange { background:#e67e22; color:#fff; }
.btn-orange:hover { background:#d35400; }
.btn-outline { background:var(--card); border:1px solid var(--border); color:var(--text); }
.btn-outline:hover:not(:disabled) { background:#f8fafc; }
.btn-sm { padding:8px 14px; font-size:13px; }

/* Log panel */
.log-panel { background:var(--card); border-radius:var(--radius); border:1px solid var(--border);
  margin-bottom:12px; }
.log-header { display:flex; align-items:center; justify-content:space-between; padding:8px 12px;
  border-bottom:1px solid var(--border); font-size:13px; font-weight:600; }
.log-box { background:#1e293b; color:#e2e8f0; padding:10px 12px; font-family:"SF Mono","Cascadia Code",monospace;
  font-size:12px; line-height:1.6; max-height:220px; overflow-y:auto; white-space:pre-wrap; word-break:break-all; }
.log-box:empty::before { content:"(Waiting for log...)"; color:#64748b; }

/* Toast */
.toast { position:fixed; bottom:20px; right:20px; padding:10px 18px; border-radius:8px;
  color:#fff; font-weight:500; z-index:200; animation:slideUp .3s; font-size:13px; }
.toast.success { background:var(--green); }
.toast.error { background:var(--red); }
@keyframes slideUp { from{opacity:0;transform:translateY(20px)} to{opacity:1;transform:translateY(0)} }

/* Progress */
.progress-bar { height:6px; background:#e2e8f0; border-radius:3px; overflow:hidden; margin:6px 0; }
.progress-fill { height:100%; border-radius:3px; background:var(--accent); transition:width .3s; width:0%; }

/* Settings modal */
.modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.4); z-index:100;
  align-items:center; justify-content:center; }
.modal-overlay.show { display:flex; }
.modal { background:var(--card); border-radius:var(--radius); padding:20px; width:90%; max-width:700px;
  max-height:85vh; overflow-y:auto; box-shadow:0 10px 40px rgba(0,0,0,.15); }
.modal h2 { margin-bottom:14px; }
.modal textarea { width:100%; min-height:80px; font-family:monospace; padding:8px; border:1px solid var(--border); border-radius:6px; font-size:13px; }
.modal-actions { display:flex; gap:8px; margin-top:14px; justify-content:flex-end; }

/* Empty state */
.empty-state { display:flex; flex-direction:column; align-items:center; justify-content:center;
  flex:1; color:var(--muted); font-size:14px; gap:8px; }
.empty-state .icon { font-size:40px; }

@media (max-width:900px) {
  .main-area { flex-direction:column; }
  .top-bar-slim { flex-direction:column; align-items:stretch; }
  .control-panel { flex-direction:column; align-items:stretch; }
}
</style>
</head>
<body>
<div class="app">
  <!-- Header -->
  <div class="header">
    <span class="status-dot idle" id="statusDot"></span>
    <h1>🎯 Internship Hunter</h1>
    <span id="headerStatus">Idle</span>
    <div class="progress-bar" style="flex:1;max-width:300px;margin-left:auto" id="progressBarOuter">
      <div class="progress-fill" id="progressFill"></div>
    </div>
  </div>

  <!-- Slim top bar: job selector -->
  <div class="top-bar-slim">
    <div>
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:3px">Select Job</label>
      <select id="jobSelector" onchange="onJobSelect()" style="min-width:360px">
        <option value="">— Select a job to view details —</option>
      </select>
    </div>
    <div style="margin-left:auto;display:flex;gap:6px;align-items:center">
      <button class="btn btn-outline btn-sm" onclick="openSettings()" title="Settings (Ctrl+,)">⚙️ Settings</button>
    </div>
  </div>

  <!-- Main area: detail panel -->
  <div class="main-area">
    <div class="detail-panel" id="detailPanel">
      <div class="empty-state" id="emptyState">
        <div class="icon">📋</div>
        <div>Select a job above to view details</div>
        <div style="font-size:12px">or click "Run" in the control panel below to start scraping</div>
      </div>
      <div id="detailContent" style="display:none;flex:1;display:flex;flex-direction:column">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
          <div>
            <h2 id="detailTitle"></h2>
            <div class="detail-meta">
              <span id="detailCompany"></span>
              <span id="detailLocation"></span>
              <span id="detailSource"></span>
            </div>
          </div>
          <a id="detailUrl" href="#" target="_blank" class="btn btn-outline btn-sm" style="text-decoration:none">🔗 Open Original</a>
        </div>

        <!-- Action buttons -->
        <div class="action-row">
          <button class="btn btn-primary btn-sm" onclick="doGenerateCL(this)">📝 Generate CL</button>
          <button class="btn btn-outline btn-sm" onclick="doEvaluate(this)">🤖 AI Evaluate</button>
          <button class="btn btn-outline btn-sm" onclick="doFetchDetail(this)">🌐 Fetch Detail</button>
          <button class="btn btn-green btn-sm" onclick="doSendEmail(this)">📧 Send Email</button>
        </div>

        <!-- Job Description -->
        <div class="detail-section">
          <h3>Job Description</h3>
          <div class="detail-text" id="detailDesc">(No description yet — click 🌐 Fetch Detail)</div>
        </div>

        <!-- AI Extracted Detail -->
        <div class="detail-section" id="structuredSection" style="display:none">
          <h3>AI Extracted Detail</h3>
          <div id="structuredContent"></div>
        </div>

        <!-- AI Evaluation -->
        <div class="detail-section" id="evalSection" style="display:none">
          <h3>AI Evaluation</h3>
          <div class="detail-text" id="evalResult"></div>
        </div>

        <!-- Cover Letter -->
        <div class="detail-section" id="clSection" style="display:none">
          <h3>Cover Letter</h3>
          <div class="detail-text" id="clContent" style="max-height:350px"></div>
          <div style="margin-top:6px;display:flex;gap:6px">
            <button class="btn btn-outline btn-sm" onclick="toggleCLEdit()">✏️ Edit CL</button>
            <button class="btn btn-outline btn-sm" onclick="doGenerateCL()">🔄 Regenerate</button>
          </div>
          <textarea id="clEditor" style="display:none;width:100%;min-height:200px;margin-top:8px;font-family:monospace;padding:8px;border:1px solid var(--border);border-radius:6px;font-size:13px"></textarea>
        </div>
      </div>
    </div>
  </div>

  <!-- Control panel: between detail and log -->
  <div class="control-panel">
    <div class="control-group">
      <div>
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:3px">Search Keywords</label>
        <input type="text" class="input-sm" id="keywordsInput" placeholder="e.g. software engineer intern summer 2026" style="width:420px">
      </div>
    </div>
    <div class="control-group check-tags" id="scraperTags">
      <label class="check-tag"><input type="checkbox" id="scraperLinkedin" checked> LinkedIn</label>
      <label class="check-tag"><input type="checkbox" id="scraperJobsdb" checked> JobsDB</label>
      <label class="check-tag"><input type="checkbox" id="scraperIndeed" checked> Indeed</label>
      <label class="check-tag"><input type="checkbox" id="scraperEfc" checked> eFC</label>
      <label class="check-tag"><input type="checkbox" id="scraperPolyu" checked> PolyU</label>
      <label class="check-tag"><input type="checkbox" id="scraperManual"> Manual</label>
    </div>
    <div class="control-group">
      <button class="btn btn-green" id="btnRun" onclick="runPipeline()">▶ Run</button>
      <button class="btn btn-red" id="btnStop" style="display:none" onclick="stopPipeline()">⏹ Stop</button>
      <button class="btn btn-orange" id="btnRestart" onclick="restartPipeline()" title="Stop + clear data + re-run">🔄 Restart</button>
    </div>
    <div class="control-group">
      <button class="btn btn-outline" onclick="generateKeywords(this)" title="Generate keywords from CV">🪄 CV Keywords</button>
      <button class="btn btn-outline" onclick="linkedinLogin()" title="Open browser to manually log in to LinkedIn (saves cookies for scraping)">🔐 LinkedIn Login</button>
    </div>
  </div>

  <!-- Log panel -->
  <div class="log-panel">
    <div class="log-header">
      <span>📜 Live Log</span>
      <button class="btn btn-outline btn-sm" onclick="clearLog()" style="font-size:11px;padding:3px 8px">Clear</button>
    </div>
    <div class="log-box" id="logBox"></div>
  </div>
</div>

<!-- Settings Modal -->
<div class="modal-overlay" id="settingsModal">
  <div class="modal">
    <h2>⚙️ Settings</h2>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">.env (credentials)</label>
      <textarea id="envEditor" rows="7" placeholder="EMAIL=...&#10;LLM_API_KEY=..."></textarea>
    </div>
    <div style="margin-bottom:12px">
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">config.yaml</label>
      <textarea id="yamlEditor" rows="18" placeholder="cv_pdf_path: ..."></textarea>
    </div>
    <div class="modal-actions">
      <button class="btn btn-primary" onclick="saveSettings()">💾 Save</button>
      <button class="btn btn-outline" onclick="closeModal('settingsModal')">Close</button>
      <span id="settingsMsg" style="font-size:12px"></span>
    </div>
  </div>
</div>

<script>
// ── State ──
let currentJobs = [];
let currentHistory = [];
let currentJobId = null;
let currentRunning = false;
let clEditMode = false;

// ── Clear log on exit ──
window.addEventListener('beforeunload', () => {
  navigator.sendBeacon('/api/clear-log');
});

// ── SSE ──
const evtSource = new EventSource("/api/sse");
let logBuffer = "";
let fullLogLoaded = false;

evtSource.onmessage = (e) => {
  try {
    const data = JSON.parse(e.data);
    if (data.full_log !== undefined) {
      document.getElementById("logBox").textContent = data.full_log || "(No log yet)";
      logBuffer = data.full_log || "";
      fullLogLoaded = true;
    }
    if (data.new_log) {
      logBuffer += data.new_log;
      const lines = logBuffer.split("\n");
      if (lines.length > 500) {
        logBuffer = lines.slice(-500).join("\n");
      }
      const box = document.getElementById("logBox");
      box.textContent = logBuffer;
      box.scrollTop = box.scrollHeight;
    }
    if (data.status) {
      currentRunning = data.running;
      updateStatusUI(data.status, data.running, data.progress_html);
    }
    if (data.jobs) {
      currentJobs = data.jobs;
      refreshJobSelector();
    }
    if (data.history) {
      currentHistory = data.history;
    }
  } catch(ex) { console.warn("SSE parse error:", ex); }
};
evtSource.onerror = () => { /* auto-reconnect */ };

// ── Job Selector ──
function refreshJobSelector() {
  const sel = document.getElementById("jobSelector");
  const prev = sel.value;
  sel.innerHTML = '<option value="">— Select a job to view details —</option>';
  currentJobs.forEach(j => {
    const opt = document.createElement("option");
    opt.value = j.id;
    const clMark = j.has_cl ? "📝" : "";
    const evalMark = j.cv_match ? (() => {
      try { return JSON.parse(j.cv_match).overall_match ? "✅" : "❌"; }
      catch(e) { return ""; }
    })() : "";
    opt.textContent = `${j.title} @ ${j.company}  ${clMark}${evalMark}`;
    sel.appendChild(opt);
  });
  if (prev && currentJobs.find(j => String(j.id) === prev)) {
    sel.value = prev;
  }
}

function onJobSelect() {
  const id = parseInt(document.getElementById("jobSelector").value);
  if (!id) {
    document.getElementById("emptyState").style.display = "flex";
    document.getElementById("detailContent").style.display = "none";
    currentJobId = null;
    return;
  }
  currentJobId = id;
  loadJobDetail(id);
}

async function loadJobDetail(id) {
  document.getElementById("emptyState").style.display = "none";
  document.getElementById("detailContent").style.display = "flex";
  const job = currentJobs.find(j => j.id === id);
  if (!job) return;

  document.getElementById("detailTitle").textContent = job.title || "(No title)";
  document.getElementById("detailCompany").textContent = job.company || "";
  document.getElementById("detailLocation").textContent = job.location || "";
  document.getElementById("detailSource").textContent = job.source || "";
  document.getElementById("detailUrl").href = job.url || "#";

  // Description
  document.getElementById("detailDesc").textContent = job.description || "(No description yet — click 🌐 Fetch Detail)";

  // CV Evaluation
  if (job.cv_match) {
    try {
      const r = JSON.parse(job.cv_match);
      const lines = [];
      lines.push(`Overall: ${r.overall_match ? "✅ Match" : "❌ Mismatch"}`);
      if (r.match_score !== undefined) lines.push(`Score: ${r.match_score}/100`);
      if (r.reasons) lines.push(`Reasons: ${r.reasons}`);
      document.getElementById("evalResult").textContent = lines.join("\n");
      document.getElementById("evalSection").style.display = "block";
    } catch(e) {
      document.getElementById("evalSection").style.display = "none";
    }
  } else {
    document.getElementById("evalSection").style.display = "none";
  }

  // Cover Letter
  loadCLForJob(id);
  // Structured Detail
  loadStructuredDetail(id);
}

async function loadCLForJob(id) {
  try {
    const res = await fetch(`/api/cover-letter/${id}`);
    const data = await res.json();
    if (data.cover_letter) {
      document.getElementById("clContent").textContent = data.cover_letter;
      document.getElementById("clEditor").value = data.cover_letter;
      document.getElementById("clSection").style.display = "block";
    } else {
      document.getElementById("clSection").style.display = "none";
    }
  } catch(e) { console.warn("CL load error", e); }
}

function displayStructured(s) {
  const el = document.getElementById("structuredContent");
  const sec = document.getElementById("structuredSection");
  if (!s) { sec.style.display = "none"; return; }
  sec.style.display = "block";
  let html = "";
  if (s.summary) {
    html += "<p><strong>Summary:</strong></p><ul>" + s.summary.split("\n").map(x => `<li>${x}</li>`).join("") + "</ul>";
  }
  if (s.requirements && s.requirements.length) {
    html += "<p><strong>Requirements:</strong></p><ul>" + s.requirements.map(x => `<li>${x}</li>`).join("") + "</ul>";
  }
  if (s.application_method) html += `<p><strong>How to apply:</strong> ${s.application_method}</p>`;
  if (s.deadline) html += `<p><strong>Deadline:</strong> ${s.deadline}</p>`;
  if (s.salary) html += `<p><strong>Salary:</strong> ${s.salary}</p>`;
  if (s.work_type) html += `<p><strong>Type:</strong> ${s.work_type}</p>`;
  if (s.location) html += `<p><strong>Location:</strong> ${s.location}</p>`;
  el.innerHTML = html;
}

async function loadStructuredDetail(id) {
  try {
    const res = await fetch(`/api/job-detail/${id}`);
    if (res.ok) {
      const data = await res.json();
      displayStructured(data.structured);
    } else {
      document.getElementById("structuredSection").style.display = "none";
    }
  } catch(e) { document.getElementById("structuredSection").style.display = "none"; }
}

// ── Actions ──
async function doGenerateCL(btn) {
  if (!currentJobId) return toast("Select a job first", "error");
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Generating..."; }
  try {
    const res = await fetch(`/api/generate-cl/${currentJobId}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      toast("Cover letter generated!", "success");
      loadCLForJob(currentJobId);
      refreshJobSelector();
    } else {
      toast(data.error || "Failed", "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
  if (btn) { btn.disabled = false; btn.textContent = "📝 Generate CL"; }
}

async function doEvaluate(btn) {
  if (!currentJobId) return toast("Select a job first", "error");
  if (btn) { btn.disabled = true; btn.textContent = "⏳"; }
  try {
    const res = await fetch(`/api/evaluate/${currentJobId}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      toast(data.message, data.overall_match ? "success" : "error");
      loadJobDetail(currentJobId);
      refreshJobSelector();
    } else {
      toast(data.error || "Failed", "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
  if (btn) { btn.disabled = false; btn.textContent = "🤖 AI Evaluate"; }
}

async function doFetchDetail(btn) {
  if (!currentJobId) return toast("Select a job first", "error");
  if (btn) { btn.disabled = true; btn.textContent = "⏳"; }
  try {
    const res = await fetch(`/api/fetch-detail/${currentJobId}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      toast("Detail fetched!", "success");
      // Display structured result immediately if available
      if (data.detail && data.detail.structured) {
        displayStructured(data.detail.structured);
      }
      loadJobDetail(currentJobId);
    } else {
      toast(data.error || "Failed", "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
  if (btn) { btn.disabled = false; btn.textContent = "🌐 Fetch Detail"; }
}

async function doSendEmail(btn) {
  if (!currentJobId) return toast("Select a job first", "error");
  if (btn) { btn.disabled = true; btn.textContent = "⏳"; }
  try {
    const res = await fetch(`/api/send-email/${currentJobId}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      toast(data.dry_run ? "✅ Email sent (Dry Run)" : "✅ Email sent!", "success");
    } else {
      toast(data.error || "Failed", "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
  if (btn) { btn.disabled = false; btn.textContent = "📧 Send Email"; }
}

// ── CL Edit ──
function toggleCLEdit() {
  const view = document.getElementById("clContent");
  const edit = document.getElementById("clEditor");
  const btn = event.target;
  if (edit.style.display === "none") {
    edit.value = view.textContent;
    view.style.display = "none";
    edit.style.display = "block";
    btn.textContent = "💾 Save CL";
  } else {
    const content = edit.value;
    fetch(`/api/cl/update/${currentJobId}`, { method: "POST", body: new URLSearchParams({content}) });
    view.textContent = content;
    view.style.display = "block";
    edit.style.display = "none";
    btn.textContent = "✏️ Edit CL";
    toast("Cover letter saved", "success");
  }
}

// ── Pipeline ──
async function runPipeline() {
  const kw = document.getElementById("keywordsInput").value;
  const fd = new FormData();
  fd.set("keywords", kw);
  fd.set("scraper_linkedin", document.getElementById("scraperLinkedin").checked);
  fd.set("scraper_jobsdb", document.getElementById("scraperJobsdb").checked);
  fd.set("scraper_indeed", document.getElementById("scraperIndeed").checked);
  fd.set("scraper_efc", document.getElementById("scraperEfc").checked);
  fd.set("scraper_polyu", document.getElementById("scraperPolyu").checked);
  fd.set("scraper_manual", document.getElementById("scraperManual").checked);
  const btn = document.getElementById("btnRun");
  btn.disabled = true;
  try {
    const res = await fetch("/api/run", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) toast(data.error || "Failed", "error");
  } catch(e) { toast("Error: " + e.message, "error"); }
  btn.disabled = false;
}

async function stopPipeline() {
  await fetch("/api/stop", { method: "POST" });
  toast("Stopping pipeline...", "success");
}

async function restartPipeline() {
  const btn = document.getElementById("btnRestart");
  if (btn) { btn.disabled = true; btn.textContent = "🔄 Restarting..."; }
  toast("Restarting: stopping current run + clearing data...", "success");
  try {
    // Stop if running
    await fetch("/api/stop", { method: "POST" }).catch(()=>{});
    // Wait a moment for stop to complete
    await new Promise(r => setTimeout(r, 1000));
    // Start fresh run
    const fd = new FormData();
    fd.set("keywords", document.getElementById("keywordsInput").value);
    fd.set("fresh", "true");
    fd.set("scraper_polyu", document.getElementById("scraperPolyu")?.checked ?? true);
    fd.set("scraper_linkedin", document.getElementById("scraperLinkedin").checked);
    fd.set("scraper_jobsdb", document.getElementById("scraperJobsdb").checked);
    fd.set("scraper_indeed", document.getElementById("scraperIndeed").checked);
    fd.set("scraper_efc", document.getElementById("scraperEfc").checked);
    fd.set("scraper_manual", document.getElementById("scraperManual").checked);
    const res = await fetch("/api/run", { method: "POST", body: fd });
    if (res.ok) {
      const data = await res.json();
      toast("Fresh run started! PID=" + (data.pid||"?"), "success");
      document.getElementById("btnRun").style.display = "none";
      document.getElementById("btnStop").style.display = "";
      document.getElementById("btnRestart").style.display = "none";
    } else {
      const data = await res.json().catch(()=>({}));
      toast("Error: " + (data.error||"Failed"), "error");
    }
  } catch(e) {
    toast("Error: " + e.message, "error");
  }
  if (btn) { btn.disabled = false; btn.textContent = "🔄 Restart"; }
}

async function generateKeywords(btn) {
  if (btn) { btn.disabled = true; btn.textContent = "⏳ Calling LLM..."; }
  try {
    const res = await fetch("/api/keywords-from-cv", { method: "POST" });
    const data = await res.json();
    if (res.ok && data.keywords) {
      document.getElementById("keywordsInput").value = data.keywords;
      toast("Keywords generated!", "success");
    } else {
      toast(data.error || "Failed", "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
  if (btn) { btn.disabled = false; btn.textContent = "🪄 CV Keywords"; }
}

async function linkedinLogin() {
  toast("Opening browser for LinkedIn login...", "success");
  try {
    const res = await fetch("/api/linkedin-login", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      toast("Browser opened! Please log in to LinkedIn manually. Cookies auto-saved after login.", "success");
    } else {
      toast("Error: " + (data.error || "Failed to launch browser"), "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
}

// ── UI Updates ──
function updateStatusUI(status, running, progressHtml) {
  const dot = document.getElementById("statusDot");
  const hdr = document.getElementById("headerStatus");
  dot.className = "status-dot " + (running ? "running" : (status.status === "error" ? "error" : "idle"));
  hdr.textContent = status.message || (running ? "Running..." : "Idle");
  document.getElementById("btnRun").style.display = running ? "none" : "";
  document.getElementById("btnStop").style.display = running ? "" : "none";
  document.getElementById("btnRestart").style.display = running ? "none" : "";
  if (progressHtml) {
    const fill = document.getElementById("progressFill");
    const match = progressHtml.match(/width:(\d+)%/);
    fill.style.width = match ? match[1] + "%" : "0%";
  }
}

function clearLog() {
  logBuffer = "";
  document.getElementById("logBox").textContent = "";
}

// ── Settings ──
async function openSettings() {
  const res = await fetch("/api/config");
  if (res.ok) {
    const d = await res.json();
    document.getElementById("envEditor").value = d.env || "";
    document.getElementById("yamlEditor").value = d.config_yaml || "";
    // Show config warnings if any
    const warnEl = document.getElementById("settingsMsg");
    if (d.warnings && d.warnings.length > 0) {
      warnEl.textContent = "⚠️ Missing: " + d.warnings.join(", ");
      warnEl.style.color = "var(--orange)";
    } else {
      warnEl.textContent = "";
    }
  }
  document.getElementById("settingsModal").classList.add("show");
}
async function saveSettings() {
  const env = document.getElementById("envEditor").value;
  const yaml = document.getElementById("yamlEditor").value;
  const res = await fetch("/api/settings", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({env, config_yaml: yaml}),
  });
  if (res.ok) {
    document.getElementById("settingsMsg").textContent = "✅ Saved!";
    document.getElementById("settingsMsg").style.color = "var(--green)";
    setTimeout(() => closeModal("settingsModal"), 800);
  } else {
    const d = await res.json();
    document.getElementById("settingsMsg").textContent = "❌ " + (d.error || "Failed");
    document.getElementById("settingsMsg").style.color = "var(--red)";
  }
}
function closeModal(id) { document.getElementById(id).classList.remove("show"); }

// ── Toast ──
function toast(msg, type) {
  const el = document.createElement("div");
  el.className = "toast " + (type || "success");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ── Keyboard shortcut ──
document.addEventListener("keydown", e => {
  if (e.ctrlKey && e.key === ",") { e.preventDefault(); openSettings(); }
});
</script>
</body>
</html>"""

@app.get("/")
async def index():
    return HTMLResponse(content=HTML_PAGE, media_type="text/html")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7861)
