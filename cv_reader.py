"""
cv_reader.py — Extract text from CV PDF and analyze with LLM.
Provides structured CV profile for job matching and cover letter generation.

Usage:
    from cv_reader import load_cv_profile
    cv_profile = load_cv_profile(config.cv_pdf_path)
    # cv_profile is a dict with: name, education, skills, projects, experience, etc.
"""

import logging
import json
import hashlib
import os
from pathlib import Path
from typing import Optional

from openai import OpenAI
from config import config

log = logging.getLogger("hunter")

# ── Prompt sent to LLM for CV analysis ──
CV_ANALYSIS_PROMPT = """You are analyzing a CV/resume. Extract the following information and return ONLY a valid JSON object.

Required fields (return as JSON):
{
  "name": "Full name",
  "email": "Email address",
  "phone": "Phone number",
  "education": [
    {"institution": "...", "program": "...", "degree": "...", "gpa": "...", "status": "..."}
  ],
  "skills": ["skill1", "skill2", ...],
  "programming_languages": ["Python", "JavaScript", ...],
  "frameworks": ["FastAPI", "React", ...],
  "ai_ml_skills": ["LLM", "RAG", "LangChain", ...],
  "tools": ["Docker", "Git", ...],
  "projects": [
    {"name": "...", "description": "...", "tech_stack": [...]}
  ],
  "experience": [
    {"role": "...", "company": "...", "duration": "...", "description": "..."}
  ],
  "languages": ["English", "Cantonese", ...],
  "year_of_study": "Year 1/2/3/final year",
  "is_final_year": false,
  "availability": "When available for internship",
  "wie_requirement": "Description of WIE requirement if mentioned",
  "summary": "A 3-sentence professional summary of this candidate"
}

Rules:
- Return ONLY the JSON, no markdown, no explanation.
- If a field is not found, use empty string or empty list.
- For skills: extract both technical and soft skills.
- For year_of_study: infer from education section.
- For is_final_year: true only if explicitly final year or Year 4/Honours Year.
- Preserve the original language (English/Chinese) of project descriptions.

CV TEXT:
"""

# ── Cache ──
_profiles_cache: dict[str, dict] = {}


def _pdf_to_text(pdf_path: str) -> str:
    """Extract all text from a PDF file."""
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader

    if not os.path.exists(pdf_path):
        log.warning(f"CV PDF not found: {pdf_path}")
        return ""

    try:
        reader = PdfReader(pdf_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        log.info(f"[CV] Extracted {len(text)} chars from {pdf_path}")
        return text
    except Exception as e:
        log.warning(f"[CV] PDF extraction failed: {e}")
        return ""


def _get_cache_key(pdf_path: str) -> str:
    """Hash of PDF file modification time + path for cache invalidation."""
    try:
        mtime = os.path.getmtime(pdf_path)
        return hashlib.md5(f"{pdf_path}:{mtime}".encode()).hexdigest()[:12]
    except Exception:
        return pdf_path


def _call_llm_analysis(cv_text: str) -> Optional[dict]:
    """Send CV text to LLM and get structured profile."""
    if not config.llm_api_key:
        log.warning("[CV] No LLM API key, skipping LLM analysis")
        return None

    if not cv_text.strip():
        log.warning("[CV] Empty CV text, skipping LLM analysis")
        return None

    try:
        client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )

        # Truncate to fit context window
        truncated = cv_text[:6000]

        response = client.chat.completions.create(
            model=config.llm_model,
            messages=[
                {"role": "system", "content": "You are a CV/resume analyzer. Return only valid JSON."},
                {"role": "user", "content": CV_ANALYSIS_PROMPT + "\n\n" + truncated},
            ],
            temperature=0.3,
            max_tokens=1500,
        )

        content = response.choices[0].message.content.strip()
        # Strip markdown code blocks if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        content = content.strip()

        profile = json.loads(content)
        log.info(f"[CV] LLM analysis complete: {profile.get('name', 'Unknown')}")
        return profile

    except json.JSONDecodeError as e:
        log.warning(f"[CV] LLM returned invalid JSON: {e}")
        return None
    except Exception as e:
        log.warning(f"[CV] LLM analysis failed: {e}")
        return None


def load_cv_profile(pdf_path: str, force_reload: bool = False) -> dict:
    """
    Load and analyze CV. Returns structured profile dict.
    Caches result based on file mtime.
    
    Args:
        pdf_path: Path to CV PDF file
        force_reload: If True, ignore cache and re-analyze
    
    Returns:
        dict with keys: name, education, skills, projects, summary, etc.
        Returns empty dict if PDF not found or analysis fails.
    """
    if not pdf_path:
        log.info("[CV] No CV PDF path configured")
        return {}

    # Resolve path
    path = Path(pdf_path)
    if not path.is_absolute():
        path = Path(__file__).parent / pdf_path

    if not path.exists():
        log.warning(f"[CV] File not found: {path}")
        return {}

    cache_key = _get_cache_key(str(path))

    # Check cache
    if not force_reload and cache_key in _profiles_cache:
        log.info("[CV] Using cached profile")
        return _profiles_cache[cache_key]

    # Step 1: Extract text
    cv_text = _pdf_to_text(str(path))
    if not cv_text:
        return {}

    # Step 2: LLM analysis
    profile = _call_llm_analysis(cv_text)

    # Step 3: Fallback — build basic profile from raw text
    if not profile:
        log.info("[CV] Using text-only profile (no LLM)")
        profile = {
            "name": "",
            "email": "",
            "phone": "",
            "education": [],
            "skills": [],
            "programming_languages": [],
            "frameworks": [],
            "ai_ml_skills": [],
            "tools": [],
            "projects": [],
            "experience": [],
            "languages": [],
            "year_of_study": "",
            "is_final_year": False,
            "availability": "",
            "wie_requirement": "312 hours WIE (PolyU)",
            "summary": "",
            "_raw_text": cv_text[:2000],  # Keep raw text for matching
        }

    # Always include raw text for matching
    if "_raw_text" not in profile:
        profile["_raw_text"] = cv_text[:3000]

    # Cache it
    _profiles_cache[cache_key] = profile
    return profile


def get_cv_text(pdf_path: str) -> str:
    """Get raw CV text (for cover letter generation)."""
    profile = load_cv_profile(pdf_path)
    if not profile:
        return ""
    return profile.get("_raw_text", "")


def format_cv_for_prompt(pdf_path: str) -> str:
    """
    Return a formatted CV summary for use in LLM prompts.
    Used by ai_writer.py to personalize cover letters.
    """
    profile = load_cv_profile(pdf_path)
    if not profile:
        return ""

    parts = []

    if profile.get("name"):
        parts.append(f"Name: {profile['name']}")

    if profile.get("education"):
        edu = profile["education"][0]  # Primary education
        parts.append(
            f"Education: {edu.get('degree', '')} in {edu.get('program', '')} "
            f"at {edu.get('institution', '')} (GPA: {edu.get('gpa', 'N/A')})"
        )

    if profile.get("skills"):
        parts.append(f"Skills: {', '.join(profile['skills'][:15])}")

    if profile.get("projects"):
        proj_str = "; ".join(
            f"{p.get('name', '')}: {p.get('description', '')[:100]}"
            for p in profile["projects"][:3]
        )
        parts.append(f"Key Projects: {proj_str}")

    if profile.get("experience"):
        exp_str = "; ".join(
            f"{e.get('role', '')} at {e.get('company', '')}"
            for e in profile["experience"][:3]
        )
        parts.append(f"Experience: {exp_str}")

    if profile.get("year_of_study"):
        parts.append(f"Year of Study: {profile['year_of_study']}")

    if profile.get("availability"):
        parts.append(f"Availability: {profile['availability']}")

    if profile.get("wie_requirement"):
        parts.append(f"WIE Requirement: {profile['wie_requirement']}")

    # Also append raw text snippet for LLM to reference
    raw = profile.get("_raw_text", "")
    if raw:
        parts.append(f"\n--- CV Text (excerpt) ---\n{raw[:1500]}")

    return "\n".join(parts)
