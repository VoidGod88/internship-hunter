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
import re
import subprocess
import sys
import threading
import time
import uuid
import yaml
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Check dependencies early ──
try:
    from fastapi import FastAPI, Form, Request, UploadFile, File
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
    import uvicorn
except ImportError as e:
    print("=" * 60)
    print("ERROR: Missing required dependencies!")
    print(f"  {e}")
    print("")
    print("Please install dependencies first:")
    print("  pip install fastapi uvicorn python-dotenv pyyaml")
    print("  pip install requests beautifulsoup4 playwright openai PyPDF2")
    print("")
    print("Or run: pip install -r requirements.txt")
    print("=" * 60)
    sys.exit(1)

# ── Project imports ──
sys.path.insert(0, str(Path(__file__).parent))
try:
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
    from mailer import send_email, get_sender_name  # noqa: E402
    import database as _db
except ImportError as e:
    print("=" * 60)
    print("ERROR: Failed to import project modules!")
    print(f"  {e}")
    print("")
    print("This usually means:")
    print("  1. Some dependencies are missing")
    print("  2. Or the project files are incomplete")
    print("")
    print("Try: pip install -r requirements.txt")
    print("=" * 60)
    sys.exit(1)

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
_polyu_login_proc: Optional[subprocess.Popen] = None
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
async def api_generate_cl(job_id: int, force: bool = False):
    try:
        jobs = get_all_jobs()
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)

        # Gate: require AI Analysis first
        detail_path = JOB_DETAILS_DIR / f"{job_id}.json"
        if not detail_path.exists():
            return JSONResponse(
                {"error": "请先运行 AI Analysis（📑 按钮）来提取职位详细信息，然后再生成 Cover Letter。"},
                status_code=400,
            )

        # ── Check cache first (unless forcing) ──
        if not force:
            existing_cl = get_cover_letter(job_id)
            if existing_cl:
                log.info(f"[Generate CL] Job {job_id}: using cached cover letter (force={force})")
                return JSONResponse({"job_id": job_id, "cover_letter": existing_cl, "cached": True})

        cfg.reload_inplace()
        cl = await asyncio.to_thread(
            generate_cover_letter,
            job.get("title", ""),
            job.get("company", ""),
            job.get("description", "") or "",
            job.get("requirements", "") or "",
            job.get("education_level", "") or "",
            job.get("url", "") or "",
            job_id,
        )
        insert_cover_letter(job_id, cl)
        return JSONResponse({"job_id": job_id, "cover_letter": cl})
    except Exception as e:
        log.exception("Generate CL error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/test-email")
async def api_test_email(request: Request):
    """Send a test email to verify SMTP config works."""
    try:
        body = await request.json()
        to_email = (body.get("to") or "").strip()
        if not to_email or "@" not in to_email:
            return JSONResponse({"error": "Valid recipient email required"}, status_code=400)

        cfg.reload_inplace()
        from mailer import send_email
        result = send_email(
            to_addr=to_email,
            subject="[Internship Hunter] Test Email",
            body=(
                "This is a test email from Internship Hunter.\n"
                "If you received this, your Gmail SMTP configuration is working correctly.\n\n"
                f"Sent from: {cfg.email or ''}\n"
                f"Sent at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            ),
            cfg=cfg,
        )
        if result["success"]:
            return JSONResponse({"success": True, "to": to_email})
        else:
            return JSONResponse({"error": result["error"]}, status_code=500)
    except Exception as e:
        log.exception("Test email error")
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

        # Try to get high-quality JD from cache (job_details/{job_id}.json)
        job_id = job.get('id', 0)
        job_desc = job.get('description', '')
        if job_id > 0:
            cache_path = JOB_DETAILS_DIR / f"{job_id}.json"
            if cache_path.exists():
                try:
                    cache = json.loads(cache_path.read_text(encoding="utf-8"))
                    cached_desc = cache.get("description", "")
                    if cached_desc and len(cached_desc) > 200:
                        log.info(f"[_evaluate_cv_match] Using cached JD from job_details/{job_id}.json ({len(cached_desc)} chars)")
                        job_desc = cached_desc
                except Exception as e:
                    log.warning(f"[_evaluate_cv_match] Failed to read cache: {e}")

        job_text = (
            f"Title: {job.get('title', '')}\n"
            f"Company: {job.get('company', '')}\n"
            f"Description: {job_desc[:2000]}\n"
            f"Requirements: {job.get('requirements', '')[:1000]}\n"
        )
        prompt = (
            "You are a strict CV screener. Compare the CV profile JSON and the job posting.\n"
            "Return ONLY a JSON object with these keys:\n"
            "  overall_match: bool (true if candidate is a good fit)\n"
            "  skills_match: bool\n"
            "  education_match: bool\n"
            "  major_match: bool\n"
            "  experience_match: bool (false if job requires experience but candidate has none)\n"
            "  match_score: int (0-100)\n"
            "  reasons: string (short explanation in Chinese)\n"
            "  requires_final_year: bool (if job requires final-year students)\n"
            "  candidate_is_final_year: bool (set to false if unknown)\n"
            "  requires_experience: bool (if job requires prior work experience, NOT internship)\n\n"
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
async def api_evaluate(job_id: int, force: bool = False):
    try:
        jobs = get_all_jobs()
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)

        # ── Check cache first (unless forcing) ──
        if not force and job.get("cv_match"):
            try:
                result = json.loads(job["cv_match"])
                details = []
                if not result.get("skills_match", True): details.append("技能不匹配")
                if not result.get("education_match", True): details.append("学历不符")
                if not result.get("major_match", True): details.append("专业不符")
                if not result.get("experience_match", True): details.append("经验不足")
                if result.get("requires_final_year", False) and not result.get("candidate_is_final_year", False):
                    details.append("要求final year")
                msg = f"✅ Cached — {'✅ Match' if result.get('overall_match') else '❌ Mismatch'} — {', '.join(details) or 'all checks passed'}"
                log.info(f"[Evaluate] Job {job_id}: using cached result (force={force})")
                return JSONResponse({"success": True, "result": result, "message": msg, "overall_match": result.get("overall_match", False), "cached": True})
            except Exception as e:
                log.warning(f"[Evaluate] Failed to parse cached cv_match for job {job_id}: {e}")
                # Fall through to LLM call

        cfg.reload_inplace()
        cv_profile = await asyncio.to_thread(load_cv_profile, cfg.cv_pdf_path, cfg)
        result = await _evaluate_cv_match(cv_profile, job)
        update_job_cv_match(job_id, json.dumps(result, ensure_ascii=False))
        details = []
        if not result.get("skills_match", True): details.append("技能不匹配")
        if not result.get("education_match", True): details.append("学历不符")
        if not result.get("major_match", True): details.append("专业不符")
        if not result.get("experience_match", True): details.append("经验不足")
        if result.get("requires_final_year", False) and not result.get("candidate_is_final_year", False):
            details.append("要求final year")
        
        # If overall mismatched but no specific detail failed, show LLM reason
        reason = result.get("reasons", "")
        if not result.get("overall_match", False) and not details and reason:
            details.append(reason)
        
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
                JOB_DETAILS_DIR.mkdir(parents=True, exist_ok=True)
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
    fresh: str = Form("false"),
    scraper_polyu: str = Form("false"),
    scraper_linkedin: str = Form("false"),
    scraper_jobsdb: str = Form("false"),
    scraper_indeed: str = Form("false"),
    scraper_efc: str = Form("false"),
    scraper_manual: str = Form("false"),
):
    # Parse bools explicitly (FastAPI bool field cannot parse "false" string)
    def _b(v: str) -> bool:
        return str(v).lower() == "true"

    _fresh = _b(fresh)
    _polyu = _b(scraper_polyu)
    _linkedin = _b(scraper_linkedin)
    _jobsdb = _b(scraper_jobsdb)
    _indeed = _b(scraper_indeed)
    _efc = _b(scraper_efc)
    _manual = _b(scraper_manual)

    global _pipeline_proc, _pipeline_running
    if _pipeline_running:
        return JSONResponse({"error": "Pipeline already running"}, status_code=409)

    # Fresh run: delete DB + status BEFORE launching hunter.py
    if _fresh:
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
    if _fresh:
        cmd.append("--fresh")
    if keywords.strip():
        cmd += ["--keywords", keywords.strip()]
    if _polyu:
        cmd.append("--scraper-polyu")
    if _linkedin:
        cmd.append("--scraper-linkedin")
    if _jobsdb:
        cmd.append("--scraper-jobsdb")
    if _indeed:
        cmd.append("--scraper-indeed")
    if _efc:
        cmd.append("--scraper-efc")
    if _manual:
        cmd.append("--scraper-manual")

    _write_status({"status": "running", "phase": "init", "message": "Starting..."})
    global _last_log_position

    # Clear log file on fresh restart for clean output
    if fresh:
        try:
            log_path = BASE_DIR / "hunter.log"
            log_path.write_text("", encoding="utf-8")
        except Exception:
            pass

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

    # 1. Write stop flag (graceful stop for hunter.py)
    try:
        from config import STOP_FLAG_PATH
        STOP_FLAG_PATH.write_text("stop", encoding="utf-8")
        log.info("[Stop] Stop flag written")
    except Exception as e:
        log.warning(f"[Stop] Failed to write stop flag: {e}")

    # 2. Kill process tree (force stop - kills Chromium children too)
    pid = _pipeline_proc.pid
    if pid:
        if sys.platform == "win32":
            # Use taskkill to kill entire process tree (including Chromium)
            try:
                kill_result = subprocess.run(
                    f"taskkill /F /T /PID {pid}",
                    shell=True, capture_output=True, text=True,
                    timeout=10,
                )
                log.info(f"[Stop] taskkill /F /T /PID {pid}: {kill_result.stdout.strip()}")
                if kill_result.stderr:
                    log.warning(f"[Stop] taskkill stderr: {kill_result.stderr.strip()}")
            except Exception as e:
                log.warning(f"[Stop] taskkill failed: {e}")
                # Fallback to terminate/kill
                _pipeline_proc.terminate()
                try:
                    _pipeline_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _pipeline_proc.kill()
                    _pipeline_proc.wait(timeout=3)
        else:
            # Linux/Mac: use process group kill
            import os
            try:
                os.killpg(os.getpgid(pid), 9)
            except Exception:
                _pipeline_proc.terminate()
                try:
                    _pipeline_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _pipeline_proc.kill()

    _pipeline_running = False
    _pipeline_proc = None
    _write_status({"status": "stopped", "phase": "stopped", "message": "Stopped by user"})
    return JSONResponse({"success": True})


@app.post("/api/settings")
async def api_save_settings(request: Request):
    try:
        content_type = request.headers.get("content-type", "")
        yaml_path = BASE_DIR / "config.yaml"

        if "application/json" in content_type:
            # New: receive structured JSON, properly update config.yaml
            data = await request.json()
            env_text = data.get("env", "")
            settings = data.get("settings", {})

            # Read existing config
            if yaml_path.exists():
                import yaml
                with open(yaml_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            else:
                config = {}

            # Update only provided fields (deep merge for dicts)
            for key, value in settings.items():
                if isinstance(value, dict) and isinstance(config.get(key), dict):
                    config[key].update(value)
                else:
                    config[key] = value

            # Write back with clean YAML
            import io
            stream = io.StringIO()
            yaml.dump(config, stream, default_flow_style=False, allow_unicode=True, sort_keys=False)
            yaml_text = stream.getvalue()
            # Clean up: ensure no trailing spaces, normalize line endings
            yaml_text = yaml_text.replace('\r\n', '\n').replace('\r', '\n')
            if not yaml_text.endswith('\n'):
                yaml_text += '\n'
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(yaml_text)

        else:
            # Legacy: raw YAML string (from advanced tab)
            form = await request.form()
            raw = form.get("config_yaml", "")
            env_text = form.get("env", "")
            if raw:
                with open(yaml_path, "w", encoding="utf-8") as f:
                    f.write(raw)

        # Write .env
        if "application/json" in content_type:
            env_text = data.get("env", "")
        if env_text:
            env_path = BASE_DIR / ".env"
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(env_text)

        cfg.reload_inplace()
        return JSONResponse({"success": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
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
    if not cfg.cv_pdf_path or "path/to" in cfg.cv_pdf_path or (cfg.cv_pdf_path and not os.path.exists(cfg.cv_pdf_path)):
        config_warnings.append("CV PDF not found — please upload or set cv_pdf_path")
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


@app.post("/api/upload-cv")
async def api_upload_cv(file: UploadFile = File(...)):
    """Upload CV PDF and update config.yaml."""
    try:
        # Save uploaded file
        filename = file.filename
        if not filename.lower().endswith(".pdf"):
            return JSONResponse({"error": "Only PDF files are allowed"}, status_code=400)

        save_path = BASE_DIR / filename
        content = await file.read()
        save_path.write_bytes(content)

        # Update config.yaml with new path
        yaml_path = BASE_DIR / "config.yaml"
        if yaml_path.exists():
            yaml_text = yaml_path.read_text(encoding="utf-8")
            # Replace cv_pdf_path line
            lines = yaml_text.split("\n")
            new_lines = []
            for line in lines:
                if line.strip().startswith("cv_pdf_path:"):
                    new_lines.append(f'cv_pdf_path: {save_path}')
                else:
                    new_lines.append(line)
            yaml_path.write_text("\n".join(new_lines), encoding="utf-8")

        # Reload config
        from importlib import reload
        import config
        reload(config)
        global cfg
        from config import config as cfg

        return JSONResponse({"success": True, "path": str(save_path)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/linkedin-login")
async def api_linkedin_login():
    """Launch linkedin_login.py in a NEW terminal window (so user can see browser + prompts)."""
    global _linkedin_login_proc
    # If already running, don't launch again
    if _linkedin_login_proc and _linkedin_login_proc.poll() is None:
        return JSONResponse({"success": True, "status": "already_running"})
    # Reset so previous finished process doesn't block re-launch
    _linkedin_login_proc = None
    login_script = str(BASE_DIR / "linkedin_login.py")
    try:
        # Open a NEW terminal window so user can see prompts and interact
        py = sys.executable
        cmd = f'start "LinkedIn Login" cmd /k ""{py}" "{login_script}""'
        _linkedin_login_proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=str(BASE_DIR),
        )
        log.info(f"LinkedIn login script started (new window)")
        return JSONResponse({"success": True})
    except Exception as e:
        log.exception("Failed to start LinkedIn login script")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/polyu-login")
async def api_polyu_login():
    """Launch polyu_login.py in a NEW terminal window (so user can see prompts)."""
    global _polyu_login_proc
    # If already running, don't launch again
    if _polyu_login_proc and _polyu_login_proc.poll() is None:
        return JSONResponse({"success": True, "status": "already_running"})
    # Reset so previous finished process doesn't block re-launch
    _polyu_login_proc = None
    login_script = str(BASE_DIR / "polyu_login.py")
    try:
        # Open a NEW terminal window so user can see prompts and press Enter
        # `cmd /k` keeps the window open after the script ends
        py = sys.executable
        cmd = f'start "PolyU Login" cmd /k ""{py}" "{login_script}""'
        _polyu_login_proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=str(BASE_DIR),
        )
        log.info(f"PolyU login script started in new window")
        return JSONResponse({"success": True})
    except Exception as e:
        log.exception("Failed to start PolyU login script")
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
<link rel="icon" href="data:,">
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
.app { max-width:1400px; margin:0 auto; padding:12px 16px; display:flex; flex-direction:column; }
.header { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid var(--border); margin-bottom:12px; }
.header h1 { font-size:18px; font-weight:700; }
.status-dot { width:10px; height:10px; border-radius:50%; display:inline-block; flex-shrink:0; }
.status-dot.idle { background:#94a3b8; }
.status-dot.running { background:var(--green); animation:pulse 1s infinite; }
.status-dot.error { background:var(--red); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
#headerStatus { font-size:13px; color:var(--muted); }

/* Slim top bar: only job selector */
.top-bar-slim { display:flex; gap:12px; align-items:center; margin-bottom:12px; flex-wrap:wrap; }
.top-bar-slim select { padding:6px 10px; border:1px solid var(--border); border-radius:6px; font-size:13px; background:var(--card); cursor:pointer; max-width:480px; min-width:160px; width:auto; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

/* Main area: detail panel — auto-height, don't stretch to fill screen */
.main-area { display:flex; gap:12px; margin-bottom:12px; }
.detail-panel { flex:1; background:var(--card); border-radius:var(--radius); border:1px solid var(--border);
  padding:16px; overflow-y:auto; display:flex; flex-direction:column; height:45vh; }
.detail-panel h2 { font-size:16px; margin-bottom:4px; }
.detail-meta { display:flex; gap:8px; flex-wrap:wrap; margin:6px 0 12px; font-size:13px; color:var(--muted); }
.detail-section { margin-bottom:14px; }
.detail-section h3 { font-size:13px; color:var(--muted); margin-bottom:6px; text-transform:uppercase; letter-spacing:.5px; }
.detail-text { font-size:13px; line-height:1.7; white-space:pre-wrap; word-break:break-word;
  border:1px solid var(--border); border-radius:6px; padding:12px; background:#f8fafc; max-height:300px; overflow-y:auto; }
#structuredContent { padding: 0 4px; }
#structuredContent p { margin: 10px 0 6px; text-align: justify; }
#structuredContent ul { padding-left: 20px; margin: 6px 0; }
#structuredContent li { margin: 4px 0; line-height: 1.7; }
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
  font-size:12px; line-height:1.6; max-height:220px; overflow-y:auto; white-space:pre-wrap; word-wrap:break-word; }
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
.modal { background:var(--card); border-radius:var(--radius); padding:20px; width:90%; max-width:720px;
  max-height:90vh; overflow-y:auto; box-shadow:0 10px 40px rgba(0,0,0,.15); }
.modal h2 { margin-bottom:4px; }
.modal .modal-desc { font-size:12px; color:var(--muted); margin-bottom:14px; }
.settings-tabs { display:flex; gap:0; margin-bottom:14px; border-bottom:2px solid var(--border); }
.settings-tab { padding:8px 16px; cursor:pointer; font-size:13px; font-weight:500; color:var(--muted);
  border-bottom:2px solid transparent; margin-bottom:-2px; transition:.15s; }
.settings-tab:hover { color:var(--text); }
.settings-tab.active { color:var(--accent); border-bottom-color:var(--accent); }
.settings-panel { display:none; }
.settings-panel.active { display:block; }
.form-group { margin-bottom:12px; }
.form-group label { display:block; font-size:12px; color:var(--muted); margin-bottom:4px; font-weight:500; }
.form-group input, .form-group textarea, .form-group select { width:100%; padding:8px 10px; border:1px solid var(--border);
  border-radius:6px; font-size:13px; background:var(--card); transition:.15s; }
.form-group input:focus, .form-group textarea:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px rgba(37,99,235,.1); }
.form-group input[type="password"] { font-family:monospace; }
.form-row { display:flex; gap:10px; }
.form-row .form-group { flex:1; }
.form-hint { font-size:11px; color:var(--muted); margin-top:3px; }
.toggle-password { position:absolute; right:8px; top:50%; transform:translateY(-50%); cursor:pointer; font-size:14px;
  user-select:none; }
.input-wrapper { position:relative; }
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
    <div style="display:flex;gap:8px;align-items:center;margin-left:auto;font-size:12px">
      <span id="cvStatus" style="color:var(--muted)">📄 CV: checking...</span>
      <span id="emailStatus" style="color:var(--muted)">✉️ Email: checking...</span>
    </div>
    <div class="progress-bar" style="flex:1;max-width:300px;margin-left:16px" id="progressBarOuter">
      <div class="progress-fill" id="progressFill"></div>
    </div>
  </div>

  <!-- Global action row: always visible, buttons disabled when no job selected -->
  <div class="top-bar-slim" id="globalActionRow" style="margin-bottom:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <button class="btn btn-outline btn-sm" id="btnAnalyze" onclick="doAnalyze(this)" disabled title="Select a job first">🤖 AI Analyze</button>
    <button class="btn btn-primary btn-sm" id="btnGenerateCL" onclick="doGenerateCL(this)" disabled title="Select a job first">📝 Generate CL</button>
    <button class="btn btn-green btn-sm" id="btnApply" onclick="openApplyModal()" disabled title="Select a job first">📧 Apply</button>
    <a id="detailUrl" href="#" target="_blank" class="btn btn-outline btn-sm" style="display:none;max-width:50%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none;flex-shrink:1">🔗 Open Original</a>
  </div>

  <div class="top-bar-slim" style="flex-wrap:wrap">
    <div style="min-width:0;overflow:hidden;max-width:65%">
      <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:3px">Select Job</label>
      <select id="jobSelector" onchange="onJobSelect()" style="max-width:420px;min-width:180px;width:auto">
        <option value="">— Select a job to view details —</option>
      </select>
    </div>
    <div style="display:flex;gap:6px;align-items:flex-end;margin-left:8px;flex-wrap:wrap">
      <button class="btn btn-outline btn-sm" id="btnMatchOverview" onclick="toggleMatchOverview()" style="white-space:nowrap">🤖 Match Overview</button>
      <button class="btn btn-outline btn-sm" id="btnAnalyzeAll" onclick="doAnalyzeAll()" title="Batch LLM match for all unevaluated jobs" style="white-space:nowrap">🤖 Analyze All</button>
      <span id="analyzeAllProgress" style="font-size:12px;color:var(--muted);display:none;white-space:nowrap"></span>
      <button class="btn btn-outline btn-sm" onclick="refreshJobs()" style="white-space:nowrap" title="Reload job list from server">🔄 Refresh</button>
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
      <div id="detailContent" style="display:none">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <h2 id="detailTitle"></h2>
            <span id="detailMatchBadge" style="font-size:13px;margin-top:2px"></span>
          </div>
            <div class="detail-meta">
              <span id="detailCompany"></span>
              <span id="detailLocation"></span>
              <span id="detailSource"></span>
            </div>
          </div>
          <!-- Open Original moved to action-row -->
        </div>

        <!-- AI Evaluation (moved above description) -->
        <div class="detail-section" id="evalSection" style="display:none">
          <h3>🤖 AI Match Result</h3>
          <div id="evalResult">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">
              <span id="evalOverallBadge" class="badge" style="font-size:13px;padding:3px 10px"></span>
              <span id="evalScore" style="font-weight:600;font-size:14px"></span>
            </div>
            <div id="evalFieldBadges" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px"></div>
            <div id="evalReasons" style="font-size:13px;line-height:1.6;margin-bottom:8px"></div>
            <div id="evalWarnings"></div>
          </div>
        </div>

        <!-- AI Extracted Detail -->
        <div class="detail-section" id="structuredSection" style="display:none">
          <h3>📑 AI Extracted Detail</h3>
          <div id="structuredContent"></div>
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

    <!-- Match Overview Panel (hidden by default) -->
    <div class="detail-panel" id="matchOverviewPanel" style="display:none;flex-direction:column;min-height:350px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <h2 style="font-size:16px">🤖 AI Match Overview</h2>
        <button class="btn btn-outline btn-sm" onclick="closeMatchOverview()">✖ Close</button>
      </div>
      <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap">
        <span class="badge badge-green" id="overviewMatchCount">✅ Match: 0</span>
        <span class="badge badge-red" id="overviewMismatchCount">❌ Mismatch: 0</span>
        <span class="badge badge-gray" id="overviewPendingCount">⏳ Pending: 0</span>
      </div>
      <div style="overflow-x:auto;flex:1">
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead>
            <tr style="background:#f8fafc;text-align:left">
              <th style="padding:6px 10px;border-bottom:2px solid var(--border)">#</th>
              <th style="padding:6px 10px;border-bottom:2px solid var(--border)">Match</th>
              <th style="padding:6px 10px;border-bottom:2px solid var(--border)">Score</th>
              <th style="padding:6px 10px;border-bottom:2px solid var(--border)">Title</th>
              <th style="padding:6px 10px;border-bottom:2px solid var(--border)">Company</th>
              <th style="padding:6px 10px;border-bottom:2px solid var(--border)">Reason</th>
            </tr>
          </thead>
          <tbody id="overviewTableBody"></tbody>
        </table>
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
    </div>
    <div class="control-group">
      <button class="btn btn-outline" onclick="generateKeywords(this)" title="Generate keywords from CV">🪄 CV Keywords</button>
      <button class="btn btn-outline" onclick="document.getElementById('cvFileInput').click()" title="Upload your CV PDF">📄 Upload CV</button>
      <input type="file" id="cvFileInput" accept=".pdf" style="display:none" onchange="uploadCV(this)">
      <button class="btn btn-outline" onclick="linkedinLogin()" title="Open browser to manually log in to LinkedIn (press Enter in terminal to save cookies)">🔐 LinkedIn Login</button>
      <button class="btn btn-outline" onclick="polyuLogin()" title="Open browser to manually log in to PolyU Job Board (saves cookies)">🏫 PolyU Login</button>
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
    <p class="modal-desc">Fill in your credentials below. Passwords are hidden by default — click 👁 to reveal.</p>

    <div class="settings-tabs">
      <div class="settings-tab active" onclick="switchSettingsTab('email')">📧 Email</div>
      <div class="settings-tab" onclick="switchSettingsTab('llm')">🤖 AI / LLM</div>
      <div class="settings-tab" onclick="switchSettingsTab('cv')">📄 CV</div>
      <div class="settings-tab" onclick="switchSettingsTab('linkedin')">🔗 LinkedIn</div>
      <div class="settings-tab" onclick="switchSettingsTab('jobsdb')">🔴 JobsDB</div>
      <div class="settings-tab" onclick="switchSettingsTab('efc')">🟢 eFC</div>
      <div class="settings-tab" onclick="switchSettingsTab('advanced')">🔧 Advanced</div>
    </div>

    <!-- Tab: Email -->
    <div class="settings-panel active" id="settingsPanel-email">
      <div class="form-group">
        <label>Email Address (Gmail)</label>
        <input type="email" id="fld_email" placeholder="your_email@gmail.com">
        <div class="form-hint">Used for sending applications. Requires Gmail App Password.</div>
      </div>
      <div class="form-group">
        <label>Email App Password</label>
        <div class="input-wrapper">
          <input type="password" id="fld_email_password" placeholder="xxxx xxxx xxxx xxxx">
          <span class="toggle-password" onclick="togglePw('fld_email_password', this)">👁</span>
        </div>
        <div class="form-hint">Generate at <a href="https://support.google.com/accounts/answer/185833" target="_blank">Google App Passwords</a>. Not your regular password!</div>
      </div>
      <hr style="margin:16px 0;border-color:#334155">
      <div class="form-group">
        <label>📤 Test Email</label>
        <div style="display:flex;gap:8px;align-items:flex-end">
          <div style="flex:1">
            <input type="email" id="fld_test_email" placeholder="test_recipient@gmail.com">
            <div class="form-hint">Sends a fixed test message to verify SMTP works.</div>
          </div>
          <button class="btn btn-green" onclick="doTestEmail(this)" style="white-space:nowrap">📤 Send Test</button>
        </div>
      </div>
    </div>

    <!-- Tab: LLM -->
    <div class="settings-panel" id="settingsPanel-llm">
      <div class="form-row">
        <div class="form-group">
          <label>LLM Provider</label>
          <input type="text" id="fld_llm_provider" placeholder="deepseek">
        </div>
        <div class="form-group">
          <label>LLM Model</label>
          <input type="text" id="fld_llm_model" placeholder="deepseek-chat">
        </div>
      </div>
      <div class="form-group">
        <label>API Key</label>
        <div class="input-wrapper">
          <input type="password" id="fld_llm_api_key" placeholder="sk-...">
          <span class="toggle-password" onclick="togglePw('fld_llm_api_key', this)">👁</span>
        </div>
      </div>
      <div class="form-group">
        <label>API Base URL</label>
        <input type="text" id="fld_llm_base_url" placeholder="https://api.deepseek.com">
        <div class="form-hint">Leave default for DeepSeek. For OpenAI, use https://api.openai.com/v1</div>
      </div>
    </div>

    <!-- Tab: CV & Keywords -->
    <div class="settings-panel" id="settingsPanel-cv">
      <div class="form-group">
        <label>CV PDF File</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input type="text" id="fld_cv_pdf_path" placeholder="path/to/your/cv.pdf" style="flex:1">
          <button class="btn btn-outline btn-sm" onclick="document.getElementById('cvFileInput2').click()">📄 Upload</button>
          <input type="file" id="cvFileInput2" accept=".pdf" style="display:none" onchange="uploadCV(this)">
        </div>
        <div class="form-hint" id="cvFileStatus">No CV uploaded yet.</div>
      </div>
    </div>

    <!-- Tab: LinkedIn Filters -->
    <div class="settings-panel" id="settingsPanel-linkedin">
      <div class="form-row">
        <div class="form-group">
          <label>Experience Level</label>
          <select id="fld_li_exp_level">
            <option value="">No Filter</option>
            <option value="1">Entry Level</option>
            <option value="2">Associate</option>
            <option value="3">Mid-Senior</option>
            <option value="4">Director</option>
            <option value="5">Executive</option>
            <option value="6">Internship</option>
          </select>
        </div>
        <div class="form-group">
          <label>Sort By</label>
          <select id="fld_li_sort_by">
            <option value="R">Relevance</option>
            <option value="DD">Most Recent</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Job Types</label>
        <div style="display:flex;gap:12px;flex-wrap:wrap;padding-top:4px">
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:normal;cursor:pointer"><input type="checkbox" value="F" id="fld_li_jt_F"> Full-time</label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:normal;cursor:pointer"><input type="checkbox" value="P" id="fld_li_jt_P"> Part-time</label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:normal;cursor:pointer"><input type="checkbox" value="I" id="fld_li_jt_I"> Internship</label>
          <label style="display:flex;align-items:center;gap:4px;font-size:13px;font-weight:normal;cursor:pointer"><input type="checkbox" value="C" id="fld_li_jt_C"> Contract</label>
        </div>
      </div>
      <div class="form-group">
        <label>Work Type</label>
        <select id="fld_li_work_types">
          <option value="1">On-site</option>
          <option value="2">Remote</option>
          <option value="3">Hybrid</option>
        </select>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Location (geoId)</label>
          <input type="text" id="fld_li_geo_id" placeholder="103291313">
          <div class="form-hint">103291313 = Hong Kong. Find others at LinkedIn.</div>
        </div>
        <div class="form-group">
          <label>Posted Within</label>
          <select id="fld_li_posted_within">
            <option value="">Any Time</option>
            <option value="past_24h">Past 24 Hours</option>
            <option value="past_week">Past Week</option>
            <option value="past_month">Past Month</option>
          </select>
        </div>
      </div>
      <div class="form-hint" style="margin-top:8px;padding:8px;background:#fef3c7;border-radius:6px">
        ⚡ Changes take effect on next scrape. These filters tell LinkedIn to only return matching jobs.
      </div>
    </div>

    <!-- Tab: JobsDB Filters -->
    <div class="settings-panel" id="settingsPanel-jobsdb">
      <div class="form-row">
        <div class="form-group">
          <label>Category</label>
          <input type="text" id="fld_jd_category" placeholder="information-communication-technology">
          <div class="form-hint">URL slug: jobs-in-{category}. Must match JobsDB category slug.</div>
        </div>
        <div class="form-group">
          <label>Work Type</label>
          <select id="fld_jd_work_type">
            <option value="on-site">On-site</option>
            <option value="remote">Remote</option>
            <option value="hybrid">Hybrid</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Date Range</label>
        <select id="fld_jd_daterange">
          <option value="">Any Time</option>
          <option value="1">Past 24 Hours</option>
          <option value="3">Past 3 Days</option>
          <option value="7">Past 7 Days</option>
          <option value="14">Past 14 Days</option>
          <option value="30">Past 30 Days</option>
        </select>
      </div>
      <div class="form-hint" style="margin-top:8px;padding:8px;background:#fef3c7;border-radius:6px">
        ⚡ JobsDB HK URL: /keyword-jobs-in-{category}/{work_type}[?daterange=N]
      </div>
    </div>

    <!-- Tab: eFC Filters -->
    <div class="settings-panel" id="settingsPanel-efc">
      <div class="form-row">
        <div class="form-group">
          <label>Experience Level</label>
          <select id="fld_efc_exp_level">
            <option value="">Any Level</option>
            <option value="NO_EXPERIENCE">No Experience</option>
            <option value="ENTRY_LEVEL">Entry Level</option>
            <option value="MID_SENIOR">Mid-Senior</option>
          </select>
        </div>
        <div class="form-group">
          <label>Sort By</label>
          <select id="fld_efc_sort_by">
            <option value="">Default</option>
            <option value="date">Most Recent</option>
            <option value="relevance">Relevance</option>
          </select>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Posted Within</label>
          <select id="fld_efc_posted_within">
            <option value="">Any Time</option>
            <option value="1">Past 24 Hours</option>
            <option value="7">Past 7 Days</option>
            <option value="14">Past 14 Days</option>
            <option value="30">Past 30 Days</option>
          </select>
        </div>
        <div class="form-group">
          <label>Page Size</label>
          <select id="fld_efc_page_size">
            <option value="15">15</option>
            <option value="25">25</option>
            <option value="50">50</option>
          </select>
        </div>
      </div>
      <div class="form-hint" style="margin-top:8px;padding:8px;background:#fef3c7;border-radius:6px">
        ⚡ eFinancialCareers HK: /jobs/{keyword}/in-hong-kong?filters.experienceLevel=...
      </div>
    </div>

    <!-- Tab: Advanced -->
    <div class="settings-panel" id="settingsPanel-advanced">
      <div class="form-group">
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">.env (raw editor)</label>
        <textarea id="envEditor" rows="6" placeholder="EMAIL=...&#10;LLM_API_KEY=..."></textarea>
        <div class="form-hint">Advanced: edit raw .env file. Changes here override the form above.</div>
      </div>
      <div class="form-group">
        <label style="font-size:12px;color:var(--muted);display:block;margin-bottom:4px">config.yaml (raw editor)</label>
        <textarea id="yamlEditor" rows="14" placeholder="cv_pdf_path: ..."></textarea>
        <div class="form-hint">Advanced: edit raw config.yaml. For scraper toggles, WIE filter, etc.</div>
      </div>
    </div>

    <div class="modal-actions">
      <button class="btn btn-primary" onclick="saveSettings()">💾 Save All</button>
      <button class="btn btn-outline" onclick="closeModal('settingsModal')">Cancel</button>
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
let evtSource = null;
let logBuffer = "";
let fullLogLoaded = false;

function connectSSE() {
  if (evtSource) {
    evtSource.close();
    evtSource = null;
  }
  evtSource = new EventSource("/api/sse");
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
  evtSource.onerror = (e) => {
    console.warn("SSE connection lost, reconnecting...", e);
    // Browser will auto-reconnect, but we force reconnect after 3s
    setTimeout(connectSSE, 3000);
  };
}
connectSSE();

// ── Job Selector ──
function getMatchRank(j) {
  // 0 = ✅ Match, 1 = ❌ Mismatch, 2 = no eval
  if (!j.cv_match) return 2;
  try {
    return JSON.parse(j.cv_match).overall_match ? 0 : 1;
  } catch(e) { return 2; }
}

function refreshJobSelector() {
  // Sort: match(✅→❌→none) → source → company → title
  const sorted = [...currentJobs].sort((a, b) => {
    const mr = getMatchRank(a) - getMatchRank(b);
    if (mr !== 0) return mr;
    const srcA = (a.source || "").toLowerCase();
    const srcB = (b.source || "").toLowerCase();
    if (srcA !== srcB) return srcA.localeCompare(srcB);
    const cmpA = (a.company || "").toLowerCase();
    const cmpB = (b.company || "").toLowerCase();
    if (cmpA !== cmpB) return cmpA.localeCompare(cmpB);
    return (a.title || "").localeCompare(b.title || "");
  });

  const sel = document.getElementById("jobSelector");
  const prev = sel.value;
  sel.innerHTML = '<option value="">— Select a job to view details —</option>';
  sorted.forEach((j, idx) => {
    const opt = document.createElement("option");
    opt.value = j.id;
    const evalMark = j.cv_match ? (() => {
      try { return JSON.parse(j.cv_match).overall_match ? "✅" : "❌"; }
      catch(e) { return ""; }
    })() : "";
    const clMark = j.has_cl ? "📝" : "";
    const srcLabel = j.source ? `[${j.source}] ` : "";
    opt.textContent = `#${idx + 1} (ID:${j.id})  ${srcLabel}${(j.title || "")} @ ${j.company || ""} ${evalMark}${clMark}`;
    sel.appendChild(opt);
  });
  // Update currentJobs to match sorted order (so onJobSelect works)
  currentJobs = sorted;
  if (prev && sorted.find(j => String(j.id) === prev)) {
    sel.value = prev;
  } else {
    // No valid previous selection — force select the empty option
    sel.value = "";
  }
  // Refresh match overview if open
  if (matchOverviewOpen) renderMatchOverview();
  // Sync UI state with selector (always run, handles both empty and selected)
  onJobSelect();
}

async function refreshJobs() {
  const btn = document.querySelector('[onclick="refreshJobs()"]');
  if (btn) { btn.disabled = true; btn.textContent = "⏳"; }
  try {
    const res = await fetch("/api/jobs");
    const data = await res.json();
    currentJobs = data.jobs || [];
    refreshJobSelector();
    toast(`Refreshed ${currentJobs.length} jobs`, "success");
  } catch(e) {
    toast("Failed to refresh: " + e.message, "error");
  }
  if (btn) { btn.disabled = false; btn.textContent = "🔄 Refresh"; }
}

function onJobSelect() {
  const sel = document.getElementById("jobSelector");
  const val = sel.value;
  // Use strict check: empty string means no selection
  if (val === "") {
    document.getElementById("emptyState").style.display = "flex";
    const dc = document.getElementById("detailContent");
    dc.style.display = "none";
    dc.style.visibility = "hidden";  // belt-and-suspenders
    // Explicitly hide all sub-sections to prevent stale content leak
    document.getElementById("evalSection").style.display = "none";
    document.getElementById("structuredSection").style.display = "none";
    document.getElementById("structuredContent").innerHTML = "";
    currentJobId = null;
    // Disable all action buttons
    ["btnAnalyze","btnGenerateCL","btnApply"].forEach(b => {
      document.getElementById(b).disabled = true;
    });
    document.getElementById("detailUrl").style.display = "none";
    return;
  }
  const id = parseInt(val);
  currentJobId = id;
  loadJobDetail(id);
}

async function loadJobDetail(id) {
  document.getElementById("emptyState").style.display = "none";
  const dc = document.getElementById("detailContent");
  dc.style.display = "flex";
  dc.style.visibility = "visible";
  const job = currentJobs.find(j => j.id === id);
  if (!job) return;

  document.getElementById("detailTitle").textContent = job.title || "(No title)";
  document.getElementById("detailCompany").textContent = job.company || "";
  document.getElementById("detailLocation").textContent = job.location || "";
  document.getElementById("detailSource").textContent = job.source || "";

  // Enable action buttons
  ["btnAnalyze","btnGenerateCL","btnApply"].forEach(b => {
    document.getElementById(b).disabled = false;
  });

  // Open Original button (global action row)
  const urlBtn = document.getElementById("detailUrl");
  if (job.url && job.url !== "#") {
    urlBtn.href = job.url;
    urlBtn.textContent = `🔗 ${job.url}`;
    urlBtn.title = job.url;
    urlBtn.style.display = "inline-block";
  } else {
    urlBtn.href = "#";
    urlBtn.textContent = "🔗 Open Original";
    urlBtn.title = "";
    urlBtn.style.display = "none";
  }

  // CV Evaluation
  const evalSec = document.getElementById("evalSection");
  const matchBadge = document.getElementById("detailMatchBadge");
  if (job.cv_match) {
    try {
      const r = JSON.parse(job.cv_match);
      // ── Overall badge ──
      const ob = document.getElementById("evalOverallBadge");
      ob.textContent = r.overall_match ? "✅ Match" : "❌ Mismatch";
      ob.className = "badge " + (r.overall_match ? "badge-green" : "badge-red");

      // ── Score ──
      const sc = document.getElementById("evalScore");
      sc.textContent = r.match_score !== undefined ? `Score: ${r.match_score}/100` : "";

      // ── Field badges (skills / education / major / experience) ──
      const fb = document.getElementById("evalFieldBadges");
      fb.innerHTML = "";
      const fields = [
        {key: "skills_match", label: "Skills"},
        {key: "education_match", label: "Education"},
        {key: "major_match", label: "Major"},
        {key: "experience_match", label: "Experience"},
      ];
      fields.forEach(f => {
        const v = r[f.key];
        if (v === undefined) return;
        const span = document.createElement("span");
        span.className = "badge " + (v ? "badge-green" : "badge-red");
        span.textContent = `${f.label}: ${v ? "✅" : "❌"}`;
        span.style.fontSize = "11px";
        fb.appendChild(span);
      });

      // ── Reasons ──
      const rs = document.getElementById("evalReasons");
      rs.textContent = r.reasons || "";

      // ── Warnings ──
      const ws = document.getElementById("evalWarnings");
      ws.innerHTML = "";
      if (r.requires_final_year && !r.candidate_is_final_year) {
        const w = document.createElement("div");
        w.style.cssText = "font-size:12px;color:var(--orange);margin-top:4px";
        w.textContent = "⚠️ Job requires final-year students — you are not marked as final year";
        ws.appendChild(w);
      }
      if (r.requires_experience) {
        const w = document.createElement("div");
        w.style.cssText = "font-size:12px;color:var(--orange);margin-top:4px";
        w.textContent = "⚠️ Job requires prior work experience (not internship)";
        ws.appendChild(w);
      }

      // ── Badge next to title ──
      matchBadge.textContent = r.overall_match ? "✅" : "❌";
      matchBadge.title = r.overall_match ? "Match" : "Mismatch";

      evalSec.style.display = "block";
    } catch(e) {
      evalSec.style.display = "none";
      matchBadge.textContent = "";
    }
  } else {
    evalSec.style.display = "none";
    matchBadge.textContent = "";
  }

  // Cover Letter
  loadCLForJob(id);
  // Structured Detail
  loadStructuredDetail(id);
}

async function loadCLForJob(id) {
  if (currentJobId !== id) return;  // stale request, discard
  try {
    const res = await fetch(`/api/cover-letter/${id}`);
    const data = await res.json();
    if (currentJobId !== id) return;
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
  // Guard: if detail panel is hidden, skip rendering
  if (!currentJobId) return;
  const el = document.getElementById("structuredContent");
  const sec = document.getElementById("structuredSection");
  if (!s) { sec.style.display = "none"; return; }
  sec.style.display = "block";
  let html = "";
  // ── Full description at top ──
  if (s.description) {
    html += `<div class="detail-text" style="margin-bottom:14px;padding:10px 14px;background:var(--bg);border-radius:8px;border:1px solid var(--border);">${s.description.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/\n/g,"<br>")}</div>`;
  }
  if (s.summary) {
    html += "<p><strong>Summary:</strong></p><ul>" + s.summary.split("\n").map(x => `<li>${x}</li>`).join("") + "</ul>";
  }
  if (s.requirements && s.requirements.length) {
    html += "<p><strong>Requirements:</strong></p><ul>" + s.requirements.map(x => `<li>${x}</li>`).join("") + "</ul>";
  }
  if (s.application_method) html += `<p><strong>How to apply:</strong> ${s.application_method}</p>`;
  if (s.application_materials) html += `<p><strong>Application Materials:</strong> ${s.application_materials}</p>`;
  if (s.deadline) html += `<p><strong>Deadline:</strong> ${s.deadline}</p>`;
  if (s.salary) html += `<p><strong>Salary:</strong> ${s.salary}</p>`;
  if (s.work_type) html += `<p><strong>Type:</strong> ${s.work_type}</p>`;
  if (s.location) html += `<p><strong>Location:</strong> ${s.location}</p>`;
  if (s.benefits) html += `<p><strong>Benefits:</strong> ${s.benefits}</p>`;
  if (s.start_date) html += `<p><strong>Start Date:</strong> ${s.start_date}</p>`;
  if (s.duration) html += `<p><strong>Duration:</strong> ${s.duration}</p>`;
  if (s.language_requirement) html += `<p><strong>Language:</strong> ${s.language_requirement}</p>`;
  if (s.visa_sponsorship) {
    const vLabel = s.visa_sponsorship === "true" ? '✅ <span style="color:#059669">Visa Sponsorship: Yes</span>' : 
                   s.visa_sponsorship === "false" ? '❌ <span style="color:#dc2626">Visa Sponsorship: No</span>' :
                   `<span style="color:#9ca3af">Visa Sponsorship: ${s.visa_sponsorship}</span>`;
    html += `<p>${vLabel}</p>`;
  }
  el.innerHTML = html;
}

async function loadStructuredDetail(id) {
  if (currentJobId !== id) return;  // stale request, discard
  try {
    const res = await fetch(`/api/job-detail/${id}`);
    if (res.ok) {
    const data = await res.json();
    if (currentJobId !== id) return;
    displayStructured(data.structured);
    } else {
      document.getElementById("structuredSection").style.display = "none";
    }
  } catch(e) { document.getElementById("structuredSection").style.display = "none"; }
}

// ── Actions (with cache support) ──
let _lastEvalJobId = null;
let _lastEvalCached = false;
let _lastCLJobId = null;
let _lastCLCached = false;

async function doGenerateCL(btn) {
  if (!currentJobId) return toast("Select a job first", "error");
  const force = (_lastCLJobId === currentJobId && _lastCLCached);
  const btnEl = document.getElementById("btnGenerateCL");
  btnEl.disabled = true; btnEl.textContent = "⏳";
  try {
    const res = await fetch(`/api/generate-cl/${currentJobId}?force=${force}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      _lastCLJobId = currentJobId;
      _lastCLCached = data.cached || false;
      toast(data.cached ? "📄 Using cached cover letter (Ctrl+click to regenerate)" : "✅ Cover letter generated!", "success");
      loadCLForJob(currentJobId);
      refreshJobSelector();
    } else {
      toast(data.error || "Failed", "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
  btnEl.disabled = false; btnEl.textContent = "📝 Generate CL";
}

async function doEvaluate(btn) {
  if (!currentJobId) return toast("Select a job first", "error");
  const force = (_lastEvalJobId === currentJobId && _lastEvalCached);
  // Show loading in evalSection
  document.getElementById("evalSection").style.display = "block";
  document.getElementById("evalOverallBadge").textContent = "⏳ Evaluating...";
  document.getElementById("evalOverallBadge").className = "badge badge-gray";
  document.getElementById("evalScore").textContent = "";
  document.getElementById("evalFieldBadges").innerHTML = "";
  document.getElementById("evalReasons").textContent = "";
  document.getElementById("evalWarnings").innerHTML = "";
  document.getElementById("detailMatchBadge").textContent = "⏳";

  if (btn) { btn.disabled = true; btn.textContent = "⏳"; }
  try {
    const res = await fetch(`/api/evaluate/${currentJobId}?force=${force}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      _lastEvalJobId = currentJobId;
      _lastEvalCached = data.cached || false;
      const label = data.cached ? "📄 Cached — " : "";
      toast(label + data.message, data.overall_match ? "success" : "error");
      // Update cv_match in currentJobs so UI reflects immediately
      const jobInList = currentJobs.find(j => j.id === currentJobId);
      if (jobInList && data.result) {
        jobInList.cv_match = JSON.stringify(data.result);
      }
      loadJobDetail(currentJobId);
      refreshJobSelector();
    } else {
      toast(data.error || "Failed", "error");
      document.getElementById("evalSection").style.display = "none";
      document.getElementById("detailMatchBadge").textContent = "";
    }
  } catch(e) { toast("Error: " + e.message, "error");
    document.getElementById("evalSection").style.display = "none";
    document.getElementById("detailMatchBadge").textContent = "";
  }
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

async function doAnalyze(btn) {
  if (!currentJobId) return toast("Select a job first", "error");
  const force = (_lastEvalJobId === currentJobId && _lastEvalCached);
  const btnEl = document.getElementById("btnAnalyze");
  btnEl.disabled = true; btnEl.textContent = "⏳";
  // Show loading in evalSection
  document.getElementById("evalSection").style.display = "block";
  document.getElementById("evalOverallBadge").textContent = "⏳ Analyzing...";
  document.getElementById("evalOverallBadge").className = "badge badge-gray";
  document.getElementById("evalScore").textContent = "";
  document.getElementById("evalFieldBadges").innerHTML = "";
  document.getElementById("evalReasons").textContent = "";
  document.getElementById("evalWarnings").innerHTML = "";
  try {
    const res = await fetch(`/api/analyze/${currentJobId}?force=${force}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      _lastEvalJobId = currentJobId;
      _lastEvalCached = data.cached || false;
      // Update currentJobs with fresh cv_match so loadJobDetail can display it
      const jobIdx = currentJobs.findIndex(j => j.id === currentJobId);
      if (jobIdx !== -1 && data.match) {
        currentJobs[jobIdx].cv_match = JSON.stringify(data.match);
      }
      toast(data.cached ? "📄 Cached — Analyze complete!" : "✅ Analyze complete!", "success");
      loadJobDetail(currentJobId);
      refreshJobSelector();
    } else {
      toast(data.error || "Failed", "error");
      document.getElementById("evalSection").style.display = "none";
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
  btnEl.disabled = false; btnEl.textContent = "🤖 AI Analyze";
}

// ── Analyze All ──
let _analyzeAllPollTimer = null;

async function doAnalyzeAll() {
  const btn = document.getElementById("btnAnalyzeAll");
  const prog = document.getElementById("analyzeAllProgress");

  // Stop if already running
  if (_analyzeAllPollTimer) {
    // Check current status
    const res = await fetch("/api/analyze-all/status");
    const s = await res.json();
    if (s.running) {
      toast("Analyze All is already running", "info");
      return;
    }
    // Was stopped/finished, clear timer
    clearInterval(_analyzeAllPollTimer);
    _analyzeAllPollTimer = null;
  }

  btn.disabled = true;
  btn.textContent = "⏳ Starting...";
  prog.style.display = "inline";

  try {
    const res = await fetch("/api/analyze-all", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      toast(data.error || "Failed to start", "error");
      btn.disabled = false;
      btn.textContent = "🤖 Analyze All";
      prog.style.display = "none";
      return;
    }
    toast("Analyze All started!", "success");
    prog.textContent = "0/0";

    // Poll progress every 1.5s
    _analyzeAllPollTimer = setInterval(async () => {
      try {
        const r = await fetch("/api/analyze-all/status");
        const s = await r.json();
        prog.textContent = `Analyzing ${s.done}/${s.total}  ${s.current ? "— " + s.current : ""}`;
        if (!s.running) {
          clearInterval(_analyzeAllPollTimer);
          _analyzeAllPollTimer = null;
          btn.disabled = false;
          btn.textContent = "🤖 Analyze All";
          const matched = s.matches || 0;
          const total = s.done || 0;
          let msg = `Done: ${total} evaluated, ${matched} matched`;
          if (s.errors) msg += `, ${s.errors} errors`;
          toast(msg, matched > 0 ? "success" : "info");
          // Refresh job list to show ✅/❌
          await refreshJobs();
          prog.textContent = `✅ ${matched}/${total} matched`;
          // Auto-hide after 5s
          setTimeout(() => { prog.style.display = "none"; }, 5000);
        }
      } catch(e) { /* network hiccup, ignore */ }
    }, 1500);
  } catch(e) {
    toast("Error: " + e.message, "error");
    btn.disabled = false;
    btn.textContent = "🤖 Analyze All";
    prog.style.display = "none";
  }
}

// ── Test Email ──
async function doTestEmail(btn) {
  const toEmail = document.getElementById('fld_test_email').value.trim();
  if (!toEmail || !toEmail.includes('@')) {
    return toast("Enter a valid recipient email", "error");
  }
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "⏳ Sending...";
  try {
    const res = await fetch("/api/test-email", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({to: toEmail})
    });
    const data = await res.json();
    if (res.ok) {
      toast("✅ Test email sent to " + toEmail, "success");
    } else {
      toast("❌ " + (data.error || "Failed to send"), "error");
    }
  } catch(e) {
    toast("Error: " + e.message, "error");
  }
  btn.disabled = false;
  btn.textContent = originalText;
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
  // ── Validate: at least one platform must be selected ──
  const anyChecked = (
    document.getElementById("scraperLinkedin").checked ||
    document.getElementById("scraperJobsdb").checked ||
    document.getElementById("scraperIndeed").checked ||
    document.getElementById("scraperEfc").checked ||
    document.getElementById("scraperPolyu").checked ||
    document.getElementById("scraperManual").checked
  );
  if (!anyChecked) {
    toast("⚠️ Please select at least one platform!", "error");
    return;
  }
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

async function generateKeywords(btn) {
  // Check if CV is available first
  const res0 = await fetch("/api/config");
  if (res0.ok) {
    const d = await res0.json();
    if (d.warnings && d.warnings.some(w => w.includes("CV PDF"))) {
      toast("⚠️ CV PDF not found! Please upload your CV first (📄 Upload CV button)", "error");
      return;
    }
  }
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
      toast("Browser opening! Please log in to LinkedIn, then press Enter in the terminal to save cookies.", "success");
    } else {
      toast("Error: " + (data.error || "Failed to launch browser"), "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
}

async function polyuLogin() {
  toast("Opening browser for PolyU login...", "success");
  try {
    const res = await fetch("/api/polyu-login", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      toast("Browser opened! Please log in to PolyU manually and accept T&C. Cookies auto-saved after login.", "success");
    } else {
      toast("Error: " + (data.error || "Failed to launch browser"), "error");
    }
  } catch(e) { toast("Error: " + e.message, "error"); }
}

async function uploadCV(input) {
  if (!input.files || !input.files[0]) return;
  const file = input.files[0];
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    toast("Only PDF files are allowed", "error");
    return;
  }
  const fd = new FormData();
  fd.set("file", file);
  try {
    const res = await fetch("/api/upload-cv", { method: "POST", body: fd });
    const data = await res.json();
    if (res.ok) {
      toast("CV uploaded: " + file.name, "success");
      // Update CV path field in settings (if open)
      const pathFld = document.getElementById("fld_cv_pdf_path");
      if (pathFld) {
        pathFld.value = data.path;
        document.getElementById("cvFileStatus").textContent = "✅ " + file.name;
      }
      // Refresh config warnings
      checkConfig();
    } else {
      toast(data.error || "Upload failed", "error");
    }
  } catch(e) {
    toast("Upload error: " + e.message, "error");
  }
  // Reset input so user can upload same file again
  input.value = "";
}

async function checkConfig() {
  const res = await fetch("/api/config");
  if (res.ok) {
    const d = await res.json();
    // Update CV status
    const cvEl = document.getElementById("cvStatus");
    if (d.warnings && d.warnings.some(w => w.includes("CV PDF"))) {
      cvEl.textContent = "📄 CV: ❌ missing";
      cvEl.style.color = "var(--red)";
    } else {
      cvEl.textContent = "📄 CV: ✅ loaded";
      cvEl.style.color = "var(--green)";
    }
    // Update email status
    const emailEl = document.getElementById("emailStatus");
    if (d.warnings && d.warnings.some(w => w.includes("Email"))) {
      emailEl.textContent = "✉️ Email: ❌ not configured";
      emailEl.style.color = "var(--red)";
    } else {
      emailEl.textContent = "✉️ Email: ✅ ready";
      emailEl.style.color = "var(--green)";
    }
  }
}

// ── UI Updates ──
function updateStatusUI(status, running, progressHtml) {
  const dot = document.getElementById("statusDot");
  const hdr = document.getElementById("headerStatus");
  dot.className = "status-dot " + (running ? "running" : (status.status === "error" ? "error" : "idle"));
  hdr.textContent = status.message || (running ? "Running..." : "Idle");
  document.getElementById("btnRun").style.display = running ? "none" : "";
  document.getElementById("btnStop").style.display = running ? "" : "none";
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
function switchSettingsTab(tab) {
  document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.settings-panel').forEach(p => p.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('settingsPanel-' + tab).classList.add('active');
}

function togglePw(fieldId, el) {
  const fld = document.getElementById(fieldId);
  if (fld.type === 'password') {
    fld.type = 'text';
    el.textContent = '🙈';
  } else {
    fld.type = 'password';
    el.textContent = '👁';
  }
}

async function openSettings() {
  const res = await fetch("/api/config");
  if (res.ok) {
    const d = await res.json();
    // Parse .env into form fields
    const envLines = (d.env || '').split('\n');
    const envMap = {};
    envLines.forEach(line => {
      const idx = line.indexOf('=');
      if (idx > 0) envMap[line.substring(0, idx).trim()] = line.substring(idx + 1).trim();
    });
    document.getElementById('fld_email').value = envMap['EMAIL'] || '';
    document.getElementById('fld_email_password').value = envMap['EMAIL_PASSWORD'] || '';
    document.getElementById('fld_llm_provider').value = envMap['LLM_PROVIDER'] || 'deepseek';
    document.getElementById('fld_llm_api_key').value = envMap['LLM_API_KEY'] || '';
    document.getElementById('fld_llm_base_url').value = envMap['LLM_BASE_URL'] || '';
    document.getElementById('fld_llm_model').value = envMap['LLM_MODEL'] || '';

    // Parse config.yaml into form fields
    const yaml = d.config_yaml || '';
    // Extract cv_pdf_path
    const cvMatch = yaml.match(/^cv_pdf_path:\s*(.*)/m);
    if (cvMatch) {
      const path = cvMatch[1].trim();
      document.getElementById('fld_cv_pdf_path').value = path;
      document.getElementById('cvFileStatus').textContent = path ? '✅ ' + path.split('/').pop().split('\\').pop() : 'No CV uploaded yet.';
    }

    // Also populate raw editors for advanced tab
    document.getElementById('envEditor').value = d.env || '';
    document.getElementById('yamlEditor').value = d.config_yaml || '';

    // Parse linkedin_filters from YAML
    const yamlText = d.config_yaml || '';
    const liSection = yamlText.match(/^linkedin_filters:[\s\S]*?(?=\n\S|\n*$)/m);
    if (liSection) {
      const liYaml = liSection[0];
      const getLi = (key, def) => { const m = liYaml.match(new RegExp('^\\s*' + key + ':\\s*["\']?(.*?)["\']?\\s*$', 'm')); return m ? m[1].trim() : def; };
      document.getElementById('fld_li_exp_level').value = getLi('experience_level', '');
      document.getElementById('fld_li_sort_by').value = getLi('sort_by', 'R');
      document.getElementById('fld_li_work_types').value = getLi('work_types', '1');
      document.getElementById('fld_li_geo_id').value = getLi('geo_id', '103291313');
      document.getElementById('fld_li_posted_within').value = getLi('posted_within', '');
      // Job types checkboxes
      const jt = getLi('job_types', 'F,P,I');
      ['F','P','I','C'].forEach(v => {
        document.getElementById('fld_li_jt_' + v).checked = jt.split(',').some(s => s.trim() === v);
      });
    }

    // Parse JobsDB filters from YAML
    const jdSection = yamlText.match(/^jobsdb_filters:[\s\S]*?(?=\n\S|\n*$)/m);
    if (jdSection) {
      const jdYaml = jdSection[0];
      const getJd = (key, def) => { const m = jdYaml.match(new RegExp('^\\s*' + key + ':\\s*["\']?(.*?)["\']?\\s*$', 'm')); return m ? m[1].trim() : def; };
      document.getElementById('fld_jd_category').value = getJd('category', 'information-communication-technology');
      document.getElementById('fld_jd_work_type').value = getJd('work_type', 'on-site');
      document.getElementById('fld_jd_daterange').value = getJd('daterange', '7');
    }

    // Parse eFC filters from YAML
    const efcSection = yamlText.match(/^efc_filters:[\s\S]*?(?=\n\S|\n*$)/m);
    if (efcSection) {
      const efcYaml = efcSection[0];
      const getEfc = (key, def) => { const m = efcYaml.match(new RegExp('^\\s*' + key + ':\\s*["\']?(.*?)["\']?\\s*$', 'm')); return m ? m[1].trim() : def; };
      document.getElementById('fld_efc_exp_level').value = getEfc('experience_level', 'NO_EXPERIENCE');
      document.getElementById('fld_efc_posted_within').value = getEfc('posted_within', '');
      document.getElementById('fld_efc_page_size').value = getEfc('page_size', '15');
      document.getElementById('fld_efc_sort_by').value = getEfc('sort_by', '');
    }

    // Show config warnings if any
    const warnEl = document.getElementById('settingsMsg');
    if (d.warnings && d.warnings.length > 0) {
      warnEl.textContent = '⚠️ ' + d.warnings.join('  ');
      warnEl.style.color = 'var(--orange)';
    } else {
      warnEl.textContent = '';
    }
  }
  document.getElementById('settingsModal').classList.add('show');
}

async function saveSettings() {
  const msgEl = document.getElementById('settingsMsg');
  msgEl.textContent = '⏳ Saving...';
  msgEl.style.color = 'var(--muted)';
  // Detect active tab
  const advancedPanel = document.getElementById('settingsPanel-advanced');
  const isAdvanced = advancedPanel && advancedPanel.classList.contains('active');
  try {
    // Build settings JSON from form fields
    const settings = {};

    // cv_pdf_path
    const cvPath = document.getElementById('fld_cv_pdf_path').value.trim();
    if (cvPath) settings['cv_pdf_path'] = cvPath;

    // linkedin_filters
    const liJobTypes = ['F','P','I','C'].filter(v => document.getElementById('fld_li_jt_' + v).checked).join(',');
    settings['linkedin_filters'] = {
      'experience_level': document.getElementById('fld_li_exp_level').value,
      'job_types': liJobTypes,
      'work_types': document.getElementById('fld_li_work_types').value,
      'geo_id': document.getElementById('fld_li_geo_id').value.trim() || '103291313',
      'sort_by': document.getElementById('fld_li_sort_by').value,
      'posted_within': document.getElementById('fld_li_posted_within').value,
    };

    // jobsdb_filters
    settings['jobsdb_filters'] = {
      'category': document.getElementById('fld_jd_category').value.trim() || 'information-communication-technology',
      'work_type': document.getElementById('fld_jd_work_type').value,
      'daterange': document.getElementById('fld_jd_daterange').value,
    };

    // efc_filters
    settings['efc_filters'] = {
      'experience_level': document.getElementById('fld_efc_exp_level').value,
      'posted_within': document.getElementById('fld_efc_posted_within').value,
      'page_size': document.getElementById('fld_efc_page_size').value,
      'sort_by': document.getElementById('fld_efc_sort_by').value,
    };

    // Build .env map
    const envMap = {};
    const envText = document.getElementById('envEditor').value;
    envText.split('\n').forEach(line => {
      const idx = line.indexOf('=');
      if (idx > 0) envMap[line.substring(0, idx).trim()] = line.substring(idx + 1).trim();
    });
    envMap['EMAIL'] = document.getElementById('fld_email').value.trim();
    envMap['EMAIL_PASSWORD'] = document.getElementById('fld_email_password').value.trim();
    envMap['LLM_PROVIDER'] = document.getElementById('fld_llm_provider').value.trim() || 'deepseek';
    envMap['LLM_API_KEY'] = document.getElementById('fld_llm_api_key').value.trim();
    envMap['LLM_BASE_URL'] = document.getElementById('fld_llm_base_url').value.trim();
    envMap['LLM_MODEL'] = document.getElementById('fld_llm_model').value.trim();
    let newEnv = '';
    const envOrder = ['EMAIL', 'EMAIL_PASSWORD', 'LLM_PROVIDER', 'LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'];
    envOrder.forEach(k => { if (envMap[k] !== undefined) newEnv += k + '=' + envMap[k] + + '\n'; });
    Object.keys(envMap).forEach(k => { if (!envOrder.includes(k)) newEnv += k + '=' + envMap[k] + + '\n'; });

    // Send as JSON (form tabs) or raw YAML (advanced tab)
    let res;
    if (isAdvanced) {
      // Advanced tab: send raw YAML
      const yaml = document.getElementById('yamlEditor').value;
      const formData = new FormData();
      formData.append('env', newEnv);
      formData.append('config_yaml', yaml);
      res = await fetch('/api/settings', {
        method: 'POST',
        body: formData,
      });
    } else {
      // Form tabs: send JSON
      res = await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({env: newEnv, settings: settings}),
      });
    }
    if (res.ok) {
      document.getElementById('settingsMsg').textContent = '✅ Saved!';
      document.getElementById('settingsMsg').style.color = 'var(--green)';
      setTimeout(() => closeModal('settingsModal'), 800);
      checkConfig();
    } else {
      const d = await res.json();
      document.getElementById('settingsMsg').textContent = '❌ ' + (d.error || 'Failed');
      document.getElementById('settingsMsg').style.color = 'var(--red)';
    }
  } catch(e) {
    document.getElementById('settingsMsg').textContent = '❌ Error: ' + e.message;
    document.getElementById('settingsMsg').style.color = 'var(--red)';
  }
}


function closeModal(id) { document.getElementById(id).classList.remove("show"); }

// ── Match Overview ──
let matchOverviewOpen = false;

function toggleMatchOverview() {
  if (matchOverviewOpen) {
    closeMatchOverview();
  } else {
    openMatchOverview();
  }
}

function openMatchOverview() {
  matchOverviewOpen = true;
  document.getElementById("detailPanel").style.display = "none";
  document.getElementById("matchOverviewPanel").style.display = "flex";
  document.getElementById("btnMatchOverview").textContent = "✖ Close Overview";
  renderMatchOverview();
}

function closeMatchOverview() {
  matchOverviewOpen = false;
  document.getElementById("matchOverviewPanel").style.display = "none";
  document.getElementById("detailPanel").style.display = "flex";
  document.getElementById("btnMatchOverview").textContent = "🤖 Match Overview";
}

function renderMatchOverview() {
  const tbody = document.getElementById("overviewTableBody");
  tbody.innerHTML = "";
  let matchCount = 0, mismatchCount = 0, pendingCount = 0;

  currentJobs.forEach((j, idx) => {
    let isMatch = null, score = "-", reason = "-", hasEval = false;
    if (j.cv_match) {
        try {
          const r = JSON.parse(j.cv_match);
          hasEval = true;
          isMatch = r.overall_match;
          score = r.match_score !== undefined ? r.match_score + "/100" : "-";
          const reasons = [];
          if (r.skills_match === false) reasons.push("技能不匹配");
          if (r.education_match === false) reasons.push("学历不符");
          if (r.major_match === false) reasons.push("专业不符");
          if (r.experience_match === false) reasons.push("经验不足");
          if (r.requires_final_year && !r.candidate_is_final_year) reasons.push("要求final year");
          reason = reasons.length ? reasons.join(", ") : (r.reasons || "-");
        } catch(e) { hasEval = false; }
      }
      if (hasEval) {
        if (isMatch) matchCount++; else mismatchCount++;
      } else {
        pendingCount++;
      }

      const tr = document.createElement("tr");
      tr.style.borderBottom = "1px solid var(--border)";
      tr.style.cursor = "pointer";
      tr.onmouseenter = () => tr.style.background = "#f8fafc";
      tr.onmouseleave = () => tr.style.background = "transparent";
      tr.onclick = () => {
        closeMatchOverview();
        document.getElementById("jobSelector").value = j.id;
        onJobSelect();
      };

      const matchBadge = !hasEval ? "⏳ Pending" :
                             isMatch ? "✅ Match" : "❌ Mismatch";
      const matchColor = !hasEval ? "var(--muted)" :
                           isMatch ? "var(--green)" : "var(--red)";

      tr.innerHTML = `
        <td style="padding:6px 10px">#${idx + 1}</td>
        <td style="padding:6px 10px;color:${matchColor};font-weight:500">${matchBadge}</td>
        <td style="padding:6px 10px">${score}</td>
        <td style="padding:6px 10px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${j.title || ''}">${j.title || '-'}</td>
        <td style="padding:6px 10px">${j.company || '-'}</td>
        <td style="padding:6px 10px;font-size:12px;color:var(--muted);max-width:250px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${reason}">${reason}</td>
      `;
      tbody.appendChild(tr);
  });

  document.getElementById("overviewMatchCount").textContent = `✅ Match: ${matchCount}`;
  document.getElementById("overviewMismatchCount").textContent = `❌ Mismatch: ${mismatchCount}`;
  document.getElementById("overviewPendingCount").textContent = `⏳ Pending: ${pendingCount}`;
}

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

// ── Init ──
window.addEventListener("load", () => {
  checkConfig();
});

// ── Poll status (update Run/Stop button) ──
let _lastRunning = null;
let _pollTimer = null;

function startPolling(interval) {
  if (_pollTimer) clearInterval(_pollTimer);
  _pollTimer = setInterval(doPoll, interval);
}

async function doPoll() {
  // Skip if page is hidden (user switched tabs)
  if (document.hidden) return;
  try {
    const res = await fetch('/api/status');
    const d = await res.json();
    const running = d.running;
    if (_lastRunning !== running) {
      _lastRunning = running;
      document.getElementById('btnRun').style.display = running ? 'none' : '';
      document.getElementById('btnStop').style.display = running ? '' : 'none';
      const dot = document.getElementById('statusDot');
      if (dot) dot.className = 'status-dot ' + (running ? 'running' : 'idle');
      // Adjust frequency: 10s when running, 30s when idle
      startPolling(running ? 10000 : 30000);
    }
  } catch(e) { /* ignore */ }
}


// ── Apply Modal ────────────────────────────────────────────────────────────

async function openApplyModal() {
    if (!currentJobId) return toast("Select a job first", "error");
    const jobs = await (await fetch("/api/jobs")).json();
    const job = (jobs.jobs || []).find(j => j.id === currentJobId);
    if (!job) return toast("Job not found", "error");

    const cl = await (await fetch(`/api/cover-letter/${currentJobId}`)).json();
    const clText = cl.cover_letter || "";
    if (!clText) return toast("Generate a Cover Letter first", "error");

    const subject = `Application for ${job.title || ""}`;
    const toAddr = (job.contact_email || "").trim();

    document.getElementById("applyTo").value = toAddr;
    document.getElementById("applySubject").value = subject;
    document.getElementById("applyBody").value = clText;
    document.getElementById("applyCvName").textContent = (cfg.cv_pdf_path || "CV.pdf").split("/").pop().split("\\").pop();
    document.getElementById("applyModal").style.display = "flex";
}


async function sendApplication() {
    const toAddr = document.getElementById("applyTo").value.trim();
    const subject = document.getElementById("applySubject").value.trim();
    const body = document.getElementById("applyBody").value.trim();
    if (!toAddr) return toast("Recipient email required", "error");
    if (!subject) return toast("Subject required", "error");
    if (!body) return toast("Email body required", "error");

    const btn = document.getElementById("applySendBtn");
    btn.disabled = true;
    btn.textContent = "⏳ Sending...";

    try {
        const res = await fetch(`/api/send-application/${currentJobId}`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({to: toAddr, subject, body}),
        });
        const data = await res.json();
        if (res.ok && data.success) {
            toast("✅ Application sent to " + toAddr, "success");
            closeApplyModal();
            refreshJobSelector();
        } else {
            toast(data.error || "Failed to send", "error");
        }
    } catch(e) {
        toast("Error: " + e.message, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "📧 Send";
    }
}


function closeApplyModal() {
    document.getElementById("applyModal").style.display = "none";
}


// Start with 30s (idle), will switch to 10s when running
startPolling(30000);

// Ensure initial state is correct on page load
document.addEventListener("DOMContentLoaded", () => {
  // Force select the empty option on page load
  const sel = document.getElementById("jobSelector");
  sel.value = "";
  onJobSelect();
});

</script>
<!-- Apply Modal (Email UI) -->
<div id="applyModal" style="display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.45);z-index:9999;justify-content:center;align-items:center;font-family:system-ui,-apple-system,sans-serif">
  <div style="background:#fff;width:100%;max-width:620px;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,0.25);overflow:hidden;display:flex;flex-direction:column;max-height:90vh">
    <!-- Modal Header -->
    <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #e5e7eb;background:#f9fafb">
      <div style="font-weight:700;font-size:15px;color:#111">📧 Send Application</div>
      <button onclick="closeApplyModal()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#9ca3af;line-height:1">&times;</button>
    </div>
    <!-- Email-like fields -->
    <div style="padding:16px 18px;overflow-y:auto;flex:1">
      <div style="margin-bottom:12px">
        <label style="display:block;font-size:12px;font-weight:600;color:#6b7280;margin-bottom:4px">To</label>
        <input id="applyTo" type="email" style="width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;outline:none" placeholder="hr@company.com">
      </div>
      <div style="margin-bottom:12px">
        <label style="display:block;font-size:12px;font-weight:600;color:#6b7280;margin-bottom:4px">Subject</label>
        <input id="applySubject" type="text" style="width:100%;padding:8px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:14px;outline:none">
      </div>
      <div style="margin-bottom:12px">
        <label style="display:block;font-size:12px;font-weight:600;color:#6b7280;margin-bottom:4px">Message</label>
        <textarea id="applyBody" rows="14" style="width:100%;padding:10px;border:1px solid #d1d5db;border-radius:6px;font-size:13px;line-height:1.6;resize:vertical;outline:none;font-family:system-ui,-apple-system,sans-serif"></textarea>
      </div>
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:6px;font-size:13px;color:#374151">
        <span>📎</span>
        <span id="applyCvName" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
        <span style="color:#9ca3af;font-size:11px">CV attached</span>
      </div>
    </div>
    <!-- Modal Footer -->
    <div style="display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid #e5e7eb;background:#f9fafb">
      <button onclick="closeApplyModal()" class="btn btn-outline btn-sm">Cancel</button>
      <button id="applySendBtn" onclick="sendApplication()" class="btn btn-green btn-sm">📧 Send</button>
    </div>
  </div>
</div>

</body>
</html>"""

@app.get("/")
async def index():
    return HTMLResponse(content=HTML_PAGE, media_type="text/html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.post("/api/send-application/{job_id}")
async def api_send_application(job_id: int, request: Request):
    """
    Send job application email with CV attachment.
    Expects JSON body: {to, subject, body} (all optional, will auto-fill from job/CV).
    """
    try:
        jobs = get_all_jobs()
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)

        body = await request.json()
        to_addr = (body.get("to") or "").strip()
        subject = (body.get("subject") or "").strip()
        email_body = (body.get("body") or "").strip()

        # Auto-fill from job / CV
        if not to_addr:
            to_addr = (job.get("contact_email") or "").strip()
        if not to_addr:
            return JSONResponse({"error": "No recipient email. Set contact_email in job or provide 'to' in request."}, status_code=400)

        if not subject:
            sender_name = get_sender_name() or "Applicant"
            subject = f"Application for {job.get('title', '')} - {sender_name}"

        if not email_body:
            # Use cached cover letter
            email_body = get_cover_letter(job_id)
        if not email_body:
            return JSONResponse({"error": "No cover letter found. Generate one first."}, status_code=400)

        cfg.reload_inplace()
        result = send_email(
            to_addr=to_addr,
            subject=subject,
            body=email_body,
            cv_path=cfg.cv_pdf_path or "",
            cfg=cfg,
        )

        if result["success"]:
            # Record in application history
            _db.record_application(job_id=job_id)
            return JSONResponse({"success": True, "to": to_addr})
        else:
            return JSONResponse({"error": result["error"]}, status_code=500)
    except Exception as e:
        log.exception("Send application error")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/analyze/{job_id}")
async def api_analyze(job_id: int, force: bool = False):
    """Unified: scrape full page → LLM returns all 22 fields at once."""
    try:
        jobs = get_all_jobs()
        job = next((j for j in jobs if j.get("id") == job_id), None)
        if not job:
            return JSONResponse({"error": "Job not found"}, status_code=404)

        # Cache: both detail file + cv_match exist
        detail_path = JOB_DETAILS_DIR / f"{job_id}.json"
        if not force and job.get("cv_match") and detail_path.exists():
            try:
                cached_detail = json.loads(detail_path.read_text(encoding="utf-8"))
                cached_match = json.loads(job["cv_match"])
                log.info(f"[Analyze] Job {job_id}: using cached result")
                return JSONResponse({
                    "success": True, "cached": True,
                    "detail": cached_detail, "match": cached_match,
                })
            except Exception:
                pass

        cfg.reload_inplace()
        cv_profile = await asyncio.to_thread(load_cv_profile, cfg.cv_pdf_path, cfg)

        from fetch_job_detail import analyze_job
        result = await asyncio.to_thread(analyze_job, cv_profile, job, cfg)

        if "error" in result:
            return JSONResponse(
                {"error": result["error"]},
                status_code=500,
            )

        # Split 22 fields into detail (12) + match (10)
        detail_fields = [
            "description", "summary", "requirements", "application_method", "application_materials",
            "deadline", "salary", "work_type", "location",
            "benefits", "start_date", "duration",
            "language_requirement", "visa_sponsorship",
        ]
        match_fields = [
            "overall_match", "skills_match", "education_match",
            "major_match", "experience_match", "match_score",
            "reasons", "requires_final_year",
            "candidate_is_final_year", "requires_experience",
        ]
        detail = {k: result.get(k) for k in detail_fields}
        match = {k: result.get(k) for k in match_fields}

        # Persist
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        detail_path.write_text(
            json.dumps(detail, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        update_job_cv_match(job_id, json.dumps(match, ensure_ascii=False))

        if result.get("description"):
            update_job_description(job_id, result["description"])

        return JSONResponse({
            "success": True, "cached": False,
            "detail": detail, "match": match,
        })

    except Exception as e:
        log.exception("[Analyze] Error")
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Analyze All (batch LLM match for all unevaluated jobs) ──

_analyze_all_state = {
    "running": False,
    "total": 0,
    "done": 0,
    "current": "",
    "errors": 0,
    "matches": 0,
}


def _run_analyze_all():
    """Background thread: iterate jobs without cv_match, call LLM, save results."""
    global _analyze_all_state
    try:
        jobs = get_all_jobs()
        # Filter: no cv_match yet
        unevaluated = [j for j in jobs if not j.get("cv_match")]
        _analyze_all_state["total"] = len(unevaluated)
        _analyze_all_state["done"] = 0
        _analyze_all_state["errors"] = 0
        _analyze_all_state["matches"] = 0
        _analyze_all_state["current"] = ""

        if not unevaluated:
            log.info("[AnalyzeAll] All jobs already evaluated")
            return

        # Load CV profile once
        cv_profile = load_cv_profile(cfg.cv_pdf_path)

        for job in unevaluated:
            job_id = job.get("id", 0)
            title = job.get("title", "?")
            _analyze_all_state["current"] = title[:50]

            try:
                result = asyncio.run(_evaluate_cv_match(cv_profile, job))
                match_json = json.dumps(result, ensure_ascii=False)
                update_job_cv_match(job_id, match_json)
                if result.get("overall_match"):
                    _analyze_all_state["matches"] += 1
            except Exception as e:
                log.warning(f"[AnalyzeAll] Job {job_id} error: {e}")
                _analyze_all_state["errors"] += 1

            _analyze_all_state["done"] += 1

        log.info(f"[AnalyzeAll] Done: {_analyze_all_state['done']}/{_analyze_all_state['total']} "
                 f"✅{_analyze_all_state['matches']} ❌{_analyze_all_state['errors']}")
    except Exception as e:
        log.exception("[AnalyzeAll] Fatal error")
    finally:
        _analyze_all_state["running"] = False


@app.post("/api/analyze-all")
async def api_analyze_all():
    """Start batch LLM match for all unevaluated jobs (non-blocking)."""
    if _analyze_all_state["running"]:
        return JSONResponse({"error": "Analyze All is already running"}, status_code=409)

    _analyze_all_state["running"] = True
    t = threading.Thread(target=_run_analyze_all, daemon=True)
    t.start()
    log.info("[AnalyzeAll] Started background thread")
    return JSONResponse({"success": True, "message": "Analyze All started"})


@app.get("/api/analyze-all/status")
async def api_analyze_all_status():
    """Poll progress of running Analyze All."""
    return JSONResponse(_analyze_all_state)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7861)
