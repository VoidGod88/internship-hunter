# 🎯 Internship Hunter v1.0

> **Automated internship hunting for PolyU WIE students** — scrape multiple platforms, AI-powered job analysis, and email applications, all from a clean web UI.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![FastAPI](https://img.shields.io/badge/Web%20UI-FastAPI-green.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

---

## 📖 What This Does

Finding a WIE (Work-Integrated Education) internship at PolyU is tedious — you need to scrape multiple job boards, check each one for CS eligibility & HK location requirements, research company details, and track your applications. **This tool automates the entire pipeline:**

```
Scrape Jobs → WIE Filter → AI Analysis → Cover Letter → Track & Send
```

| Step | What Happens |
|------|-------------|
| 🔍 **Scrape** | Pulls jobs from 6 platforms simultaneously |
| 🎯 **WIE Filter** | Strict WIE filter per PolyU COMP FAQ — ineligible jobs are **discarded**, not saved |
| 🤖 **AI Match** | On-demand LLM evaluation: compares your CV against job requirements |
| 🌐 **Fetch Detail** | Re-opens job page, extracts structured fields (description, requirements, salary...) via LLM — with smart caching |
| ✍️ **Cover Letter** | AI-generated, personalized cover letter (DeepSeek / OpenAI) |
| ✉️ **Apply** | Review, edit, and send applications via Gmail SMTP |
| 📊 **Track** | Records everything in SQLite — no duplicate applications |

---

## ✨ Features

- **6 Job Sources** — LinkedIn, JobsDB, Indeed HK, eFinancialCareers, PolyU Job Board, Manual company list
- **🎯 Strict WIE Filter** — 8 rules based on PolyU COMP WIE FAQ; ineligible jobs are discarded before entering the database
- **🤖 On-Demand AI Match** — LLM-powered CV-vs-job evaluation (skills, education, major, experience), triggered per-job from the UI
- **🔐 LinkedIn Cookie Login** — One-click browser login saves cookies, bypassing Cloudflare detection
- **🎮 CV-Generated Keywords** — One-click button extracts search keywords from your CV and fills the keyword input
- **📄 Job Detail + Smart Cache** — Fetch full job description via LLM; caches to `job_details/{job_id}.json` to avoid re-fetching
- **📝 AI Cover Letters** — DeepSeek / OpenAI compatible API; generates role-specific, personalized cover letters (reuses cached job details)
- **✉️ Test Email** — Built-in test email function to verify Gmail SMTP config before sending real applications
- **🌐 FastAPI Web UI** — Clean native HTML/JS interface: configure, run pipeline, review jobs, send emails — all in one page
- **⚙️ Built-in Config Panel** — Edit all settings (.env and config.yaml) directly in the UI
- **📤 CV Upload** — Drag-and-drop CV PDF upload in the UI; auto-saves to project directory
- **📊 SQLite Database** — Persistent job history, duplicate detection, cover letter storage, CV-match results, send history
- **📝 Live Log** — Real-time log viewer in the UI during pipeline execution
- **🚦 Progress Bar** — Visual pipeline phase indicator (Init → Scraping → Processing → Done)
- **🔒 Security First** — `.env` and `config.yaml` are gitignored; never commit real credentials

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Playwright** (Chromium) — for LinkedIn / detail fetching
- **Gmail account** with [App Password](https://support.google.com/accounts/answer/185833) — for sending emails
- **Optional:** DeepSeek or OpenAI API key — for AI cover letters and CV analysis

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/VoidGod88/internship-hunter.git
cd internship-hunter

# 2. Create virtual environment & install dependencies
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate

pip install -r requirements.txt
python -m playwright install chromium

# 3. Launch the web UI
python web_ui.py
# Open http://localhost:7861 in your browser
# On first run, .env and config.yaml will be auto-created with default values
# Fill in credentials in the ⚙️ Settings panel
```

---

## ⚙️ Configuration

### `.env` — Credentials

Fill in via the **⚙️ Settings** panel in the web UI, or create a `.env` file manually:

```env
EMAIL=your_email@gmail.com
EMAIL_PASSWORD=your_gmail_app_password

LLM_PROVIDER=deepseek
LLM_API_KEY=your_deepseek_api_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
```

> **Note:** `.env` is gitignored. The repo ships without real credentials.

### `config.yaml` — Settings

All settings editable in the **⚙️ Settings** panel. Defaults are pre-filled — adjust keywords, scrapers, and filters to your needs.

```yaml
keywords: "software engineer intern, data analyst intern, AI intern"
wie_enabled: true
platforms:
  linkedin: true
  jobsdb: true
  indeed: true
  efc: true
  manual: true
```

---

## 🎯 WIE Filter Rules

Based on PolyU COMP WIE FAQ — ineligible jobs are **discarded** at the filtering stage and never enter the database.

| # | Rule | Source | Logic |
|---|------|--------|-------|
| 1 | STEM Internship Scheme | FAQ #11 | Title contains "STEM" + "intern/scheme/program" → discard |
| 2 | Freelance / Private Tutoring | FAQ #5 | Contains "freelance", "private tutor", "家教" → discard |
| 3 | IT Sales | FAQ #5 | Title is "IT sales" (little software dev) → discard |
| 4 | Technician (no software) | FAQ #5 | Title is "technician" but no "software/dev/engineer" keywords → discard |
| 5 | Non-CS/IT role | FAQ #3, #6 | Negative keywords (clerk, data entry, driver...) or no CS keywords |
| 6 | Not in Hong Kong | FAQ #4 | Location not in HK districts list |
| 7 | Not internship/summer | FAQ #4 | Title lacks "intern/internship/trainee/summer" keywords |
| 8 | Final year required | FAQ #6 (toggle) | Excluded only when `wie_exclude_final_year: true` |

---

## 🖥️ UI Overview

```
┌─────────────────────────────────────────────────────┐
│  [Logo] Internship Hunter v1.0        ⚙️ Settings │
├──────────────────────┬──────────────────────────────┤
│  Job List (left)     │  Job Detail (right)          │
│  ┌────────────────┐  │  ┌────────────────────────┐ │
│  │ Dropdown select │  │  │ Title, Company, URL    │ │
│  │ Job rows       │  │  │ AI Match Score          │ │
│  │ [WIE?] [CV♟] │  │  │ Description + CL Editor │ │
│  └────────────────┘  │  └────────────────────────┘ │
├──────────────────────┴──────────────────────────────┤
│  Control Panel                                      │
│  [Keywords...] [♟ CV Keywords] [🔐 LinkedIn Login] │
│  [✓ LinkedIn] [✓ JobsDB] [✓ Indeed] [✓ eFC]    │
│  [✓ Manual]                                        │
│  [▶ Run] [⏹ Stop]                                │
├─────────────────────────────────────────────────────┤
│  Live Log (collapsible)                           │
└─────────────────────────────────────────────────────┘
```

### Key Actions

| Button | What it does |
|--------|---------------|
| **🎮 CV Keywords** | Extracts search keywords from your uploaded CV via LLM |
| **🔐 LinkedIn Login** | Opens browser for manual LinkedIn login; saves cookies (bypasses Cloudflare) |
| **▶ Run** | Starts the scraping pipeline |
| **🤖 AI Match** | Evaluates selected job against your CV (skills, education, match score) |
| **📄 Fetch Detail** | Re-opens job page, extracts structured info via LLM (cached after first run) |
| **✨ Generate CL** | Generates AI cover letter for the selected job (reuses cached job details) |
| **📧 Test Email** | Sends a test email to verify SMTP config |
| **✉️ Apply** | Sends application email with CV attachment |

---

## 🏗️ Architecture

```
internship-hunter/
├── web_ui.py               # FastAPI web UI (main entry point)
├── hunter.py                # Core pipeline: scrape → WIE filter → dedup → save
├── config.py                # Config loader (.env + config.yaml)
├── database.py              # SQLite ORM (jobs, cover_letters, history)
├── models.py                # Job dataclass
├── ai_writer.py            # LLM cover letter generator (OpenAI-compatible API)
├── mailer.py               # Gmail SMTP sender (test email + get_sender_name)
├── cv_reader.py            # CV PDF text extraction + LLM keyword extraction
├── fetch_job_detail.py     # Re-open job URL, extract full detail via LLM (with cache)
├── linkedin_login.py       # Standalone script: manual LinkedIn login, saves cookies
├── stealth.py              # Playwright stealth utils (anti-detection)
├── manual_companies.json   # Custom company list for manual scraping
│
├── scrapers/               # Job board scrapers
│   ├── __init__.py
│   ├── base.py             # Base scraper with Playwright page init + cookie loading
│   ├── linkedin.py         # LinkedIn job search (uses saved cookies)
│   ├── jobsdb.py           # JobsDB (Hong Kong)
│   ├── indeed.py           # Indeed HK ( Cloudflare retry + keyword filtering)
│   ├── efc.py              # eFinancialCareers (infinite scroll with smart stop)
│   └── manual.py           # Manual company list scraper
│
├── cookies/                 # Saved browser cookies (gitignored)
├── data/                    # Job detail JSON cache (gitignored)
├── .env                     # Credentials (gitignored)
├── config.yaml              # Settings (gitignored)
├── requirements.txt         # Python dependencies
└── README.md
```

### Pipeline Flow

```
Step 1: CV Parsing (UI, 1× LLM)
  ["🎮 CV Keywords" button]
  → Extract {technical, domains, roles} from CV
  → Fill keyword input

Step 2: Scrape (pipeline, 0× LLM)
  Run → Scrape 6 platforms
  → Strict WIE filter (8 rules)
  → Discard ineligible jobs
  → Dedup + Save WIE-eligible only

Step 3: Review (user-driven)
  Select job → View details
  → ["🤖 AI Match"] (1× LLM)
  → ["📄 Fetch Detail"] (1× LLM, cached after first run)
  → View AI-extracted job info

Step 4: Apply (manual)
  ["✨ Generate CL"] → AI cover letter (reuses cached job details)
  → Review & edit
  → ["✉️ Apply"] → Send via Gmail SMTP
```

### Smart Caching (v1.0 New)

Both **AI Match** and **Generate CL** now reuse `job_details/{job_id}.json` cache:
- First time: re-opens job URL, extracts full page via LLM → saves to cache
- Subsequent times: reads directly from cache → **no re-fetching, no duplicate LLM calls**

---

## 🔐 LinkedIn Cloudflare Bypass

LinkedIn has strong Cloudflare protection that blocks automated scrapers. This project solves it by:

1. **Cookie-Based Auth** — Use the "🔐 LinkedIn Login" button to manually log in (including passing any Cloudflare challenge)
2. **Cookie Persistence** — Cookies are saved to `cookies/linkedin.json`
3. **Automatic Cookie Loading** — Future scraping runs automatically load saved cookies, bypassing Cloudflare

> **Note:** Cookies expire after some time. If scraping fails, re-run "🔐 LinkedIn Login".

---

## 📋 Requirements

| Dependency | Version | Purpose |
|-----------|---------|---------|
| Python | >= 3.10 | Runtime |
| fastapi | >= 0.110 | Web UI backend |
| uvicorn | >= 0.29 | ASGI server |
| playwright | >= 1.44 | LinkedIn + job detail fetching |
| openai | >= 1.0 | LLM API client (DeepSeek/OpenAI) |
| requests | >= 2.31 | HTTP client (scrapers) |
| beautifulsoup4 | >= 4.12 | HTML parsing |
| PyYAML | >= 6.0 | Config file parsing |
| python-dotenv | >= 1.0 | Environment variable loading |
| PyPDF2 | >= 3.0 | CV PDF text extraction |

---

## 📝 v1.0 Changelog

### Added
- **Smart Job Detail Cache** — `fetch_job_detail()` now caches to `job_details/{job_id}.json`; AI Writer and AI Evaluate both reuse the cache to avoid duplicate fetching
- **Test Email Function** — Settings panel now has a "Test Email" section; enter recipient email → sends fixed test message via Gmail SMTP to verify config
- **Stealth Module** — Added `stealth.py` for Playwright anti-detection (used by all scrapers)

### Fixed
- **eFC scraper** — Fixed `seen_hrefs` global pollution bug (per-keyword dedup sets were shared, causing cross-keyword filtering)
- **eFC infinite scroll** — Upgraded from `keyboard.press("End")` to `Stealth.human_scroll()` with smart stop condition (4 rounds no new cards)
- **Indeed Cloudflare** — Added retry logic (detect "Just a moment..." challenge, wait 15s, retry up to 2×)
- **Indeed keyword filtering** — Added client-side title filtering to remove recommended jobs that don't match search keywords
- **`config` vs `cfg` naming** — Fixed `api_test_email` using `config.email` instead of `cfg.email` (caused NameError)

### Removed
- **Dry run mode** — Removed `dry_run` parameter from `mailer.py`, `database.py`, and `config.py` (replaced by Test Email function)
- **Indeed Settings tab** — Removed (Indeed filter `sc=0kf:attr()` format too complex to reverse-engineer)

---

## 📝 License
