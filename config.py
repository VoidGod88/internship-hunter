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
"""
        ENV_PATH.write_text(content, encoding="utf-8")
        print(f"[Config] Created {ENV_PATH} — please edit it with your credentials")

    # config.yaml
    yaml_example = BASE_DIR / "config.yaml.example"
    if not CONFIG_PATH.exists():
        if yaml_example.exists():
            print(f"[Config] config.yaml not found, copying from {yaml_example}...")
            import shutil
            shutil.copy(str(yaml_example), str(CONFIG_PATH))
        else:
            print("[Config] ERROR: config.yaml.example not found!")
            print("[Config] Please create config.yaml manually or restore config.yaml.example from git.")
            sys.exit(1)


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

    # ── General ──
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

    # ── LinkedIn search filters ──
    li_exp_level: str = "1"           # 1=Entry, 2=Associate, 3=Mid-Senior, 4=Director, 5=Executive, 6=Internship
    li_job_types: str = "F,P,I"       # F=Full-time, P=Part-time, I=Internship, C=Contract, T=Temporary, V=Volunteer
    li_work_types: str = "1"          # 1=On-site, 2=Remote, 3=Hybrid
    li_geo_id: str = "103291313"      # LinkedIn geo ID (103291313=Hong Kong)
    li_sort_by: str = "R"             # R=Relevance, DD=Most recent
    li_posted_within: str = ""        # past_24h, past_week, past_month, or empty for any time

    # ── JobsDB search filters ──
    jd_category: str = "information-communication-technology"
    jd_work_type: str = "on-site"     # on-site, remote, hybrid
    jd_daterange: str = "7"           # 1, 3, 7, 14, 30, or empty

    # ── Indeed search filters ──
    id_date_range: str = ""           # fromage: 7, 14, 30, or empty
    id_job_type: str = ""             # internship, fulltime, parttime, contract, or empty
    id_sort_by: str = ""              # date, relevance
    id_radius: str = ""               # km radius

    # ── eFC search filters ──
    efc_exp_level: str = "NO_EXPERIENCE"  # NO_EXPERIENCE, ENTRY_LEVEL, MID_SENIOR, etc.
    efc_posted_within: str = ""           # 1, 7, 14, 30, or empty
    efc_page_size: str = "15"             # max 50
    efc_sort_by: str = ""                 # date, relevance, or empty for default

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

        # ── From config.yaml (settings) ──
        cfg.cv_pdf_path = _yaml_config.get("cv_pdf_path", "")
        # dry_run removed — test email now in Settings
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

        # ── LinkedIn Filters ──
        li_filters = _yaml_config.get("linkedin_filters", {})
        if li_filters:
            cfg.li_exp_level = str(li_filters.get("experience_level", "1"))
            cfg.li_job_types = str(li_filters.get("job_types", "F,P,I"))
            cfg.li_work_types = str(li_filters.get("work_types", "1"))
            cfg.li_geo_id = str(li_filters.get("geo_id", "103291313"))
            cfg.li_sort_by = str(li_filters.get("sort_by", "R"))
            cfg.li_posted_within = str(li_filters.get("posted_within", ""))

        # ── JobsDB Filters ──
        jd_filters = _yaml_config.get("jobsdb_filters", {})
        if jd_filters:
            cfg.jd_category = str(jd_filters.get("category", "information-communication-technology"))
            cfg.jd_work_type = str(jd_filters.get("work_type", "on-site"))
            cfg.jd_daterange = str(jd_filters.get("daterange", "7"))

        # ── Indeed Filters ──
        id_filters = _yaml_config.get("indeed_filters", {})
        if id_filters:
            cfg.id_date_range = str(id_filters.get("date_range", ""))
            cfg.id_job_type = str(id_filters.get("job_type", ""))
            cfg.id_sort_by = str(id_filters.get("sort_by", ""))
            cfg.id_radius = str(id_filters.get("radius", ""))

        # ── eFC Filters ──
        efc_filters = _yaml_config.get("efc_filters", {})
        if efc_filters:
            cfg.efc_exp_level = str(efc_filters.get("experience_level", "NO_EXPERIENCE"))
            cfg.efc_posted_within = str(efc_filters.get("posted_within", ""))
            cfg.efc_page_size = str(efc_filters.get("page_size", "15"))
            cfg.efc_sort_by = str(efc_filters.get("sort_by", ""))

        return cfg

    def to_dict(self) -> dict:
        """Return a dict for Gradio UI display / editing."""
        return {
            "email": self.email,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
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
        # Re-load .env with override=True so updated values replace cached ones
        load_dotenv(ENV_PATH, override=True)
        # Re-load config.yaml
        global _yaml_config
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, encoding="utf-8") as f:
                _yaml_config = yaml.safe_load(f) or {}
        new = Config.load()
        self.__dict__.update(new.__dict__)


# ── Global config instance ──
config = Config.load()
