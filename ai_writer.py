"""
ai_writer.py — LLM-powered cover letter generator.
Supports DeepSeek, OpenAI, and any OpenAI-compatible API.

Now uses real CV text (from cv_reader) for personalized cover letters.
"""

import logging
from openai import OpenAI
from config import config
from cv_reader import format_cv_for_prompt, load_cv_profile

log = logging.getLogger("hunter")

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


def generate_cover_letter(job_title: str, company: str, description: str,
                          requirements: str = "", education: str = "") -> str:
    """
    Generate a personalized cover letter using LLM.
    Uses real CV text for personalization.
    Falls back to template-based generation if LLM is unavailable.
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
        name = profile.get("name", "Yip Fung Ming")
        phone = profile.get("phone", "5169-3118")
        email = profile.get("email", "yipfm88@gmail.com")
        year = profile.get("year_of_study", "Year 3")
        github = "github.com/VoidGod88"
    except Exception:
        name = "Yip Fung Ming"
        phone = "5169-3118"
        email = "yipfm88@gmail.com"
        year = "Year 3"
        github = "github.com/VoidGod88"

    desc_lower = (job_title + " " + description).lower()

    if any(k in desc_lower for k in ["agent", "multi-agent", "llm", "gpt", "coze", "langchain", "rag"]):
        ai_para = (
            "I specialise in agentic AI systems: I built a multi-agent teaching assistant "
            "with live code execution (Docker-sandboxed FastAPI), "
            "LLM-based rubric grading, and long-term memory. "
            "I am proficient with prompt engineering, tool calling, and orchestrating "
            "reliable multi-step AI workflows."
        )
    elif any(k in desc_lower for k in ["machine learning", "deep learning", "data", "ml", "kaggle"]):
        ai_para = (
            "I have hands-on ML experience from Kaggle competitions and coursework in AI. "
            "I am comfortable with Python, scikit-learn, data augmentation, and cross-validation."
        )
    else:
        ai_para = (
            "My most significant project is an AI teaching assistant built with modern "
            "LLM orchestration, featuring sandboxed code execution and automated grading."
        )

    return (
        f"Dear Hiring Manager,\n\n"
        f"Re: Application for {job_title} – Summer 2026 Internship\n\n"
        f"I am writing to express my strong interest in the {job_title} position at {company}. "
        f"I am {name}, a {year} BSc Computer Science student at The Hong Kong "
        f"Polytechnic University (PolyU), seeking a summer internship to fulfil my Work-Integrated "
        f"Education (WIE) requirement (312 hours).\n\n"
        f"{ai_para}\n\n"
        f"I use modern AI-augmented development workflows (vibe coding with Claude Code, Codex, "
        f"and Cursor), allowing me to iterate fast and deliver working software. "
        f"As a non-final year student, I am eager to learn and contribute to {company}'s "
        f"projects over the summer period.\n\n"
        f"I would greatly appreciate the opportunity to discuss how I can contribute to {company}. "
        f"Please find my CV attached. I am available for Summer 2026 (June–August).\n\n"
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
            getattr(job, 'education_level', '')
        )
        results.append({
            "job": job,
            "cover_letter": cl,
        })
    return results
