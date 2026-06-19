"""
config.py — Unified configuration loader
Loads from .env (secrets) + config.yaml (settings), merges into one Config object.
"""

import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yaml

# ── Try to import dotenv, show helpful error if missing ──
try:
    from dotenv import load_dotenv
except ImportError:
    print("[Config] ERROR: python-dotenv not installed. Run: pip install python-dotenv")
    print("[Config] Alternatively, create .env file manually with your credentials")
    load_dotenv = lambda x: None  # no-op fallback

# ── Paths ──
BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config.yaml"
STOP_FLAG_PATH = BASE_DIR / "stop.flag"

def check_stop():
    """Raise InterruptedError if stop.flag exists. Call in long loops."""
    if STOP_FLAG_PATH.exists():
        STOP_FLAG_PATH.unlink()  # delete so next run doesn't immediately stop
        raise InterruptedError("Stop requested by user")

# ── Auto-create config files if missing ──
def _ensure_config_files():
    """Create default .env and config.yaml if they don't exist."""
    # .env
    env_example = BASE_DIR / ".env.example"
    if not ENV_PATH.exists():
        print("[Config] .env not found, creating default...")
        if env_example.exists():
            content = env_example.read_text(encoding="utf-8")
        else:
            content = """# Email credentials (for sending applications)
EMAIL=your_email@gmail.com
EMAIL_PASSWORD=your_google_app_password

# LLM provider for cover letter generation
LLM_PROVIDER=deepseek
LLM_API_KEY=your_api_key_here
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# PolyU Jobboard (optional, leave blank if not using)
POLYU_NET_ID=your_net_id
POLYU_PASSWORD=your_password
"""
        ENV_PATH.write_text(content, encoding="utf-8")
        print(f"[Config] Created {ENV_PATH} — please edit it with your credentials")

    # config.yaml
    yaml_example = BASE_DIR / "config.yaml.example"
    if not CONFIG_PATH.exists():
        print("[Config] config.yaml not found, creating default...")
        if yaml_example.exists():
            content = yaml_example.read_text(encoding="utf-8")
        else:
            content = """# Path to your CV PDF (used for keyword extraction and CV matching)
cv_pdf_path: path/to/your/cv.pdf

# Search keywords for job scraping
search_keywords:
  - summer internship 2026 computer science
  - software engineer intern summer 2026

# Scraper toggles (set to false to disable)
scrapers:
  linkedin: true
  jobsdb: true
  indeed: true
  efinancialcareers: true
  manual_companies: true
  polyu: true

# WIE filter settings (PolyU-specific)
wie_filter:
  enabled: true
  require_hk_location: true
  exclude_non_cs: true
  exclude_final_year_required: true

# CV matching settings
cv_matching:
  enabled: true
  match_education: true
  match_skills: true
  match_final_year: true

# Cover letter generation
cover_letter:
  enabled: true
  language: en

# Email settings
email_settings:
  subject_template: Application for {job_title} – Summer Internship
  attach_cv: true
  delay_seconds: 5
"""
        CONFIG_PATH.write_text(content, encoding="utf-8")
        print(f"[Config] Created {CONFIG_PATH} — please edit it with your settings")


_ensure_config_files()

# ── Load .env ──
load_dotenv(ENV_PATH)

# ── Load config.yaml ──
_yaml_config = {}
if CONFIG_PATH.exists():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        _yaml_config = yaml.safe_load(f) or {}


@dataclass
class Config:
    # ── Email ──
    email: str = ""
    email_password: str = ""
    cv_pdf_path: str = ""

    # ── LLM ──
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"

    # ── PolyU ──
    polyu_net_id: str = ""
    polyu_password: str = ""

    # ── General ──
    dry_run: bool = True
    max_emails_per_run: int = 10

    # ── Keywords ──
    search_keywords: list[str] = field(default_factory=list)

    # ── Scraper flags ──
    scraper_polyu: bool = True
    scraper_linkedin: bool = True
    scraper_jobsdb: bool = True
    scraper_indeed: bool = True
    scraper_efc: bool = True
    scraper_manual: bool = True

    # ── WIE ──
    wie_enabled: bool = True
    wie_require_hk: bool = True
    wie_exclude_non_cs: bool = True
    wie_exclude_final_year: bool = True

    # ── AI Scoring ──
    ai_scoring_enabled: bool = True
    ai_min_score_email: int = 15

    # ── Cover Letter ──
    cover_letter_enabled: bool = True
    cover_letter_language: str = "en"

    # ── CV Matching ──
    cv_matching_enabled: bool = True
    cv_match_education: bool = True
    cv_match_skills: bool = True
    cv_match_final_year: bool = True

    # ── Email settings ──
    email_subject_template: str = "Summer 2026 Internship – {title} | {name} (PolyU CS)"
    email_attach_cv: bool = True
    email_delay_seconds: int = 5

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()

        # ── From .env (secrets) ──
        cfg.email = os.getenv("EMAIL", "")
        cfg.email_password = os.getenv("EMAIL_PASSWORD", "")
        cfg.llm_provider = os.getenv("LLM_PROVIDER", "deepseek")
        cfg.llm_api_key = os.getenv("LLM_API_KEY", "")
        cfg.llm_base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
        cfg.llm_model = os.getenv("LLM_MODEL", "deepseek-chat")
        cfg.polyu_net_id = os.getenv("POLYU_NET_ID", "")
        cfg.polyu_password = os.getenv("POLYU_PASSWORD", "")

        # ── From config.yaml (settings) ──
        cfg.cv_pdf_path = _yaml_config.get("cv_pdf_path", "")
        cfg.dry_run = _yaml_config.get("dry_run", True)
        cfg.max_emails_per_run = _yaml_config.get("max_emails_per_run", 10)
        cfg.search_keywords = _yaml_config.get("search_keywords", [])

        scrapers = _yaml_config.get("scrapers", {})
        if scrapers:
            cfg.scraper_polyu = scrapers.get("polyu_jobboard", True)
            cfg.scraper_linkedin = scrapers.get("linkedin", True)
            cfg.scraper_jobsdb = scrapers.get("jobsdb", True)
            cfg.scraper_indeed = scrapers.get("indeed", True)
            cfg.scraper_efc = scrapers.get("efinancialcareers", True)
            cfg.scraper_manual = scrapers.get("manual_companies", True)

        wie = _yaml_config.get("wie_filter", {})
        if wie:
            cfg.wie_enabled = wie.get("enabled", True)
            cfg.wie_require_hk = wie.get("require_hk_location", True)
            cfg.wie_exclude_non_cs = wie.get("exclude_non_cs", True)
            cfg.wie_exclude_final_year = wie.get("exclude_final_year_required", True)

        ai_scoring = _yaml_config.get("ai_scoring", {})
        if ai_scoring:
            cfg.ai_scoring_enabled = ai_scoring.get("enabled", True)
            cfg.ai_min_score_email = ai_scoring.get("min_score_for_email", 15)

        cl = _yaml_config.get("cover_letter", {})
        if cl:
            cfg.cover_letter_enabled = cl.get("enabled", True)
            cfg.cover_letter_language = cl.get("language", "en")

        # ── CV Matching ──
        cv_matching = _yaml_config.get("cv_matching", {})
        if cv_matching:
            cfg.cv_matching_enabled = cv_matching.get("enabled", True)
            cfg.cv_match_education = cv_matching.get("match_education", True)
            cfg.cv_match_skills = cv_matching.get("match_skills", True)
            cfg.cv_match_final_year = cv_matching.get("match_final_year", True)

        email_cfg = _yaml_config.get("email_settings", _yaml_config.get("email", {}))
        if isinstance(email_cfg, dict):
            cfg.email_subject_template = email_cfg.get(
                "subject_template",
                "Summer 2026 Internship – {title} | {name} (PolyU CS)"
            )
            cfg.email_attach_cv = email_cfg.get("attach_cv", True)
            cfg.email_delay_seconds = email_cfg.get("delay_seconds", 5)

        return cfg

    def to_dict(self) -> dict:
        """Return a dict for Gradio UI display / editing."""
        return {
            "email": self.email,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "dry_run": self.dry_run,
            "max_emails_per_run": self.max_emails_per_run,
            "search_keywords": ", ".join(self.search_keywords),
            "scraper_polyu": self.scraper_polyu,
            "scraper_linkedin": self.scraper_linkedin,
            "scraper_jobsdb": self.scraper_jobsdb,
            "scraper_indeed": self.scraper_indeed,
            "scraper_efc": self.scraper_efc,
            "scraper_manual": self.scraper_manual,
            "wie_enabled": self.wie_enabled,
            "cover_letter_enabled": self.cover_letter_enabled,
            "cv_matching_enabled": self.cv_matching_enabled,
        }

    def reload_inplace(self):
        """Reload config from .env + config.yaml into THIS instance."""
        new = Config.load()
        self.__dict__.update(new.__dict__)


# ── Global config instance ──
config = Config.load()
