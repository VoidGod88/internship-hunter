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

You will receive:
1. CANDIDATE CV — the candidate's CV text (extract relevant skills/projects)
2. JOB DATA (JSON) — structured information about the job, including:
   - summary: bullet points describing the role
   - requirements: list of specific requirements (version numbers, years of experience, etc.)
   - application_method: how to apply (email/portal)
   - application_materials: what to attach (CV, transcript, portfolio, etc.)
   - benefits, start_date, duration, language_requirement, visa_sponsorship
   - location, salary, work_type
3. RAW JOB DESCRIPTION — the full original job posting text (use this to avoid missing any details)

How to use the JOB DATA JSON:
- requirements: Mention 1-2 specific requirements from this list that the candidate matches (e.g. "As required, I have 3 years of Python experience...")
- application_materials: If the job needs specific materials, mention the candidate has prepared them (e.g. "I have attached my CV, transcript, and portfolio as requested.")
- visa_sponsorship: If "false", explicitly mention the candidate has work authorization / is a local student (e.g. "I am a local student with work authorization in Hong Kong.")
- language_requirement: If specified, mention the candidate meets the language requirement.
- benefits / start_date / duration: Only mention these if they are unusual/attractive and relevant to why the candidate wants THIS specific role.

Structure:
1. Opening: State the position and your interest. Mention the company specifically.
2. Body: Connect your skills/projects to the job requirements. Mention 1-2 specific projects that are most relevant. Reference specific technologies from requirements.
3. Closing: Express enthusiasm, mention availability (WIE 312 hours for non-final-year), request an interview.

Rules:
- Be specific to the job — reference their tech stack, domain, or specific requirements from the JSON
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


def _get_high_quality_jd(job_id: int) -> dict:
    """
    Load job data from cache (job_details/{job_id}.json).
    Returns dict with keys: description (raw text), structured (dict of all cached fields).
    """
    result = {"description": "", "structured": {}}
    if job_id <= 0:
        return result
    if not JOB_DETAILS_DIR.exists():
        return result
    cache_path = JOB_DETAILS_DIR / f"{job_id}.json"
    if not cache_path.exists():
        return result
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        result["description"] = cache.get("description", "")
        structured = {k: v for k, v in cache.items() if k != "description"}
        result["structured"] = structured
        log.info(f"[AI Writer] Loaded cache job_details/{job_id}.json ({len(str(structured))} chars structured)")
    except Exception as e:
        log.warning(f"[AI Writer] Failed to read cache {cache_path}: {e}")
    return result


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

    if not cv_text:
        log.warning("No CV text available, using template fallback")
        return _template_cover_letter(job_title, company, description)

    # Load job data from cache (description + structured fields)
    job_data = _get_high_quality_jd(job_id)
    cache_desc = job_data.get("description", "")
    structured = job_data.get("structured", {})

    # Use cached description if available (more complete than passed description)
    if cache_desc and len(cache_desc) > 200:
        description = cache_desc
        log.info(f"[AI Writer] Using cached description ({len(description)} chars)")

    # Build prompt with JSON block + raw description
    structured_json = json.dumps(structured, ensure_ascii=False, indent=2) if structured else "{}"

    user_prompt = f"""{CV_USER_INFO}

========== CANDIDATE CV ==========
{cv_text[:5000]}

========== JOB DATA (JSON) ==========
{structured_json}

========== RAW JOB DESCRIPTION ==========
{description[:5000]}

========== TASK ==========
Write the cover letter now, using the most relevant CV information and the job data above.
Reference specific requirements from the JSON. If application_materials is specified, mention the candidate has prepared them.
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
