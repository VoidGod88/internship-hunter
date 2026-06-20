"""
ai_writer.py — LLM-powered cover letter generator.
Supports DeepSeek, OpenAI, and any OpenAI-compatible API.

Uses real CV text (from cv_reader) for personalization.
Reuses fetch_job_detail() from AI Evaluate for high-quality JD extraction.
Checks job_details/{job_id}.json cache before re-fetching.
"""

import json
import logging
from pathlib import Path
from openai import OpenAI
from config import config
from cv_reader import format_cv_for_prompt, load_cv_profile

log = logging.getLogger("hunter")

# Try to import fetch_job_detail for high-quality JD extraction (reuse AI Evaluate logic)
try:
    from fetch_job_detail import fetch_job_detail
    _HAS_FETCH_DETAIL = True
except ImportError:
    _HAS_FETCH_DETAIL = False
    log.warning("[ai_writer] fetch_job_detail not available, using scraped description only")

# Job details cache directory (same as web_ui.JOB_DETAILS_DIR)
JOB_DETAILS_DIR = Path(__file__).parent / "data" / "job_details"

# ── System Prompt (dynamic CV injected at runtime) ──
BASE_SYSTEM_PROMPT = """You are helping a student write a professional, personalized cover letter for an internship.

TASK: Write a professional, concise, and personalized cover letter for the specific internship job.
The cover letter should be 200-300 words, in plain text (no markdown).

Structure:
1. Opening: State the position and your interest
2. Body: Connect your skills/projects to the job requirements. Mention 1-2 specific projects that are most relevant.
3. Closing: Express enthusiasm, mention availability, request an interview.

Rules:
- Be specific to the job description — reference their tech stack, domain, or requirements
- Keep it professional but personable
- Mention if you are a non-final-year student seeking WIE placement (312 hours)
- Include your contact info at the end
- NO markdown, NO placeholders like [Your Name], NO generic templates
- Write the actual letter content only, ready to send
"""

CV_USER_INFO = """
The candidate's CV information is provided below. Use the MOST RELEVANT parts of their CV
to write a targeted cover letter — do NOT try to mention everything. Focus on what
directly matches the job description.
"""


def _get_client() -> OpenAI:
    """Create OpenAI-compatible client."""
    return OpenAI(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
    )


def _get_high_quality_jd(original_desc: str, url: str, job_id: int) -> str:
    """
    Get high-quality JD, reusing AI Evaluate cache + fetch_job_detail().
    Priority:
    1. job_details/{job_id}.json (cache from AI Evaluate) → use description
    2. fetch_job_detail(url) → fetch + cache
    3. fallback to original_desc
    """
    # Step 1: Check cache (job_details/{job_id}.json)
    if job_id > 0 and JOB_DETAILS_DIR.exists():
        cache_path = JOB_DETAILS_DIR / f"{job_id}.json"
        if cache_path.exists():
            try:
                cache = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_desc = cache.get("description", "")
                if cached_desc and len(cached_desc) > 200:
                    log.info(f"[AI Writer] Using cached JD from job_details/{job_id}.json ({len(cached_desc)} chars)")
                    return cached_desc
                # Also check if structured has description
                structured = cache.get("structured", {})
                if isinstance(structured, dict):
                    # structured doesn't have description, but AI Evaluate saves description separately
                    pass
            except Exception as e:
                log.warning(f"[AI Writer] Failed to read cache {cache_path}: {e}")

    # Step 2: Fetch from URL (reuse AI Evaluate logic)
    if url and _HAS_FETCH_DETAIL:
        try:
            log.info(f"[AI Writer] Fetching high-quality JD from URL: {url[:60]}")
            fetch_result = fetch_job_detail(url)
            fetched_desc = fetch_result.get("description", "")
            if fetched_desc and len(fetched_desc) > 200:
                log.info(f"[AI Writer] Using fetched JD ({len(fetched_desc)} chars)")
                # Save to cache for next time
                if job_id > 0:
                    try:
                        JOB_DETAILS_DIR.mkdir(parents=True, exist_ok=True)
                        cache_path = JOB_DETAILS_DIR / f"{job_id}.json"
                        cache = {}
                        if cache_path.exists():
                            cache = json.loads(cache_path.read_text(encoding="utf-8"))
                        cache["description"] = fetched_desc
                        cache_path.write_text(
                            json.dumps(cache, ensure_ascii=False, indent=2),
                            encoding="utf-8"
                        )
                    except Exception:
                        pass
                return fetched_desc
            else:
                log.info(f"[AI Writer] Fetched JD too short, using original")
        except Exception as e:
            log.warning(f"[AI Writer] Failed to fetch JD from URL: {e}")

    # Step 3: Fallback to original description
    return original_desc


def generate_cover_letter(job_title: str, company: str, description: str,
                          requirements: str = "", education: str = "",
                          url: str = "", job_id: int = 0) -> str:
    """
    Generate a personalized cover letter using LLM.
    Uses real CV text for personalization.
    Reuses fetch_job_detail() from AI Evaluate for high-quality JD extraction.
    Checks job_details/{job_id}.json cache before re-fetching.
    """
    if not config.llm_api_key:
        log.warning("No LLM API key configured, using template-based cover letter")
        return _template_cover_letter(job_title, company, description)

    # Load CV text dynamically
    cv_text = ""
    if config.cv_pdf_path:
        cv_text = format_cv_for_prompt(config.cv_pdf_path)

    # Try to get high-quality JD (reuse AI Evaluate logic + cache)
    description = _get_high_quality_jd(description, url, job_id)

    if not cv_text:
        log.warning("No CV text available, using template fallback")
        return _template_cover_letter(job_title, company, description)

    user_prompt = f"""{CV_USER_INFO}

========== CANDIDATE CV ==========
{cv_text[:3000]}

========== JOB DETAILS ==========
Position: {job_title}
Company: {company}

Job Description:
{description[:1500]}

Requirements:
{requirements[:500]}

Education Requirements:
{education[:200]}

========== TASK ==========
Write the cover letter now, using the most relevant CV information:
"""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=config.llm_model,
            messages=[
                {"role": "system", "content": BASE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        content = response.choices[0].message.content.strip()
        log.info(f"AI cover letter generated for {company} - {job_title[:40]}")
        return content

    except Exception as e:
        log.warning(f"LLM failed ({e}), falling back to template")
        return _template_cover_letter(job_title, company, description)


def _template_cover_letter(job_title: str, company: str, description: str) -> str:
    """Template-based fallback cover letter (no LLM required)."""
    # Try to load CV profile for personalization
    try:
        profile = load_cv_profile(config.cv_pdf_path)
        name = profile.get("name", "[Your Name]")
        phone = profile.get("phone", "[Your Phone]")
        email = profile.get("email", "[Your Email]")
        year = profile.get("year_of_study", "Year 3")
        github = profile.get("github", "[Your GitHub]")
    except Exception:
        name = "[Your Name]"
        phone = "[Your Phone]"
        email = "[Your Email]"
        year = "Year 3"
        github = "[Your GitHub]"

    desc_lower = (job_title + " " + description).lower()

    if any(k in desc_lower for k in ["agent", "multi-agent", "llm", "gpt", "prompt", "rag"]):
        ai_para = (
            "I have experience building AI-powered applications and working with large language models. "
            "I am comfortable with prompt engineering, API integration, and orchestrating multi-step AI workflows."
        )
    elif any(k in desc_lower for k in ["machine learning", "deep learning", "data", "ml", "kaggle"]):
        ai_para = (
            "I have hands-on experience with machine learning projects and data analysis. "
            "I am comfortable with Python, scikit-learn, and model evaluation workflows."
        )
    else:
        ai_para = (
            "I enjoy building practical software solutions and have experience with modern development workflows. "
            "I am a fast learner and adaptable to new technologies and team practices."
        )

    return (
        f"Dear Hiring Manager,\n\n"
        f"Re: Application for {job_title} – Summer Internship\n\n"
        f"I am writing to express my strong interest in the {job_title} position at {company}. "
        f"I am {name}, a {year} BSc Computer Science student, seeking a summer internship "
        f"to fulfil my Work-Integrated Education (WIE) requirement.\n\n"
        f"{ai_para}\n\n"
        f"I use modern development tools and AI-augmented workflows to iterate quickly and deliver working software. "
        f"I am eager to learn and contribute to {company}'s projects over the summer period.\n\n"
        f"I would greatly appreciate the opportunity to discuss how I can contribute to {company}. "
        f"Please find my CV attached.\n\n"
        f"Yours sincerely,\n"
        f"{name}\n"
        f"Tel: {phone} | Email: {email} | GitHub: {github}"
    )


def generate_batch(jobs: list) -> list:
    """Generate cover letters for a batch of jobs. Returns dicts with job info + cover letter."""
    results = []
    for i, job in enumerate(jobs):
        log.info(f"Generating cover letter {i+1}/{len(jobs)}: {job.company} - {job.title[:40]}")
        cl = generate_cover_letter(
            job.title, job.company, job.description,
            getattr(job, 'requirements', ''),
            getattr(job, 'education_level', ''),
            getattr(job, 'url', ''),
            getattr(job, 'id', 0),
        )
        results.append({
            "job": job,
            "cover_letter": cl,
        })
    return results
