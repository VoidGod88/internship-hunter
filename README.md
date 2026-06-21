# 🎯 Internship Hunter

> **Automated internship hunting for PolyU WIE students** — scrape multiple platforms, AI-powered job matching, and email applications, all from a clean web UI.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![FastAPI](https://img.shields.io/badge/Web%20UI-FastAPI-green.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

---

## 📖 What This Does

Finding a WIE (Work-Integrated Education) internship at PolyU is tedious — you need to scrape multiple job boards, check each one for CS eligibility & HK location requirements, research company details, and track your applications. **This tool automates the entire pipeline:**

```
Scrape Jobs → WIE Filter → AI Match → Analyze Detail → Cover Letter → Apply
```

| Step | What Happens |
|------|-------------|
| 🔍 **Scrape** | Pulls jobs from 5 platforms simultaneously |
| 🎯 **WIE Filter** | Strict WIE filter per PolyU COMP FAQ — ineligible jobs are **discarded**, not saved |
| 🤖 **Analyze All** | Batch LLM evaluation: one-click match ALL jobs against your CV, skip already-matched |
| 📊 **Match Overview** | Table view: ✅/❌ match status, scores, and mismatch reasons for all jobs at a glance |
| 📑 **AI Analyze** | Per-job detail: fetches structured fields (description, requirements, salary...) via LLM, cached to disk |
| ✍️ **Cover Letter** | AI-generated personalized cover letter (DeepSeek / OpenAI), reuses cached job detail |
| ✉️ **Apply** | Review, edit, and send applications via Gmail SMTP |
| 📊 **Track** | Records everything in SQLite — no duplicate applications |

---

## ✨ Features

- **5 Job Sources** — LinkedIn, JobsDB, Indeed HK, eFinancialCareers, Manual company list
- **🎯 Strict WIE Filter** — 8 rules based on PolyU COMP WIE FAQ; ineligible jobs are discarded before entering the database
- **🤖 Analyze All** — One-click batch LLM matching with real-time progress (`45/150 — Company Name...`); skips already-matched jobs
- **📊 Match Overview** — Sortable table: ✅/❌/⏳ status, scores, mismatch reasons; click any row to jump to that job
- **📑 Job Detail + Smart Cache** — Fetch full job description via LLM; caches to `data/job_details/{job_id}.json` to avoid re-fetching
- **🔐 LinkedIn Cookie Login** — One-click browser login saves cookies, bypassing Cloudflare detection
- **🎮 CV-Generated Keywords** — One-click button extracts search keywords from your CV and fills the keyword input
- **📝 AI Cover Letters** — DeepSeek / OpenAI compatible API; generates role-specific, personalized cover letters
- **🔒 Generate CL Gate** — Cover letter generation requires running AI Analysis first (backend check + toast prompt), ensuring high-quality input
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
- **Optional:** DeepSeek or OpenAI API key — for AI cover letters and CV matching

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
cv_pdf_path: path/to/your/cv.pdf
search_keywords:
  - summer internship 2026 computer science
  - software engineer intern summer 2026
scrapers:
  linkedin: true
  jobsdb: true
  indeed: true
  efinancialcareers: true
  manual_companies: true
wie_filter:
  enabled: true
  require_hk_location: true
  exclude_non_cs: true
  exclude_final_year_required: true
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
┌──────────────────────────────────────────────────────┐
│  [Logo] Internship Hunter     ⚙️ Settings  [Status] │
├──────────────────────┬───────────────────────────────┤
│  Job List (left)     │  Job Detail (right)           │
│  ┌────────────────┐  │  ┌─────────────────────────┐ │
│  │ [Select Job ▼] │  │  │ Title, Company, URL     │ │
│  │ [Match Overview│  │  │ ✅/❌ CV Match + Detail  │ │
│  │  Analyze All]   │  │  │ Description + CL Editor  │ │
│  │ Job rows       │  │  │ [Generate CL] [Analyze] │ │
│  │ ✅/❌ per job  │  │  └─────────────────────────┘ │
│  └────────────────┘  │                               │
├──────────────────────┴───────────────────────────────┤
│  Control Panel                                       │
│  [Keywords...] [🎮 CV Keywords] [🔐 LinkedIn Login] │
│  [✓ LinkedIn] [✓ JobsDB] [✓ Indeed] [✓ eFC]     │
│  [✓ Manual]                                        │
│  [▶ Run] [⏹ Stop]                                  │
├──────────────────────────────────────────────────────┤
│  Action Row                           [🔗 Open Orig] │
│  [📝 Generate CL] [🤖 AI Analyze] [📧 Apply]      │
├──────────────────────────────────────────────────────┤
│  Live Log (collapsible)                              │
└──────────────────────────────────────────────────────┘
```

### Key Actions

| Button | What it does |
|--------|---------------|
| **🤖 Match Overview** | Opens sortable table of all jobs with ✅/❌/⏳ match status, scores, and reasons |
| **🤖 Analyze All** | Batch LLM evaluation for all unevaluated jobs with real-time progress |
| **🎮 CV Keywords** | Extracts search keywords from your uploaded CV via LLM |
| **🔐 LinkedIn Login** | Opens browser for manual LinkedIn login; saves cookies (bypasses Cloudflare) |
| **▶ Run** | Starts the scraping pipeline |
| **🤖 AI Analyze** | Fetches full job detail via LLM, displays structured info + full JD (cached after first run); **required before Generate CL** |
| **📝 Generate CL** | Generates AI cover letter (reuses AI Analyze cached data); gated: requires AI Analyze first |
| **📧 Apply** | Opens email preview modal; review & send application via Gmail SMTP |
| **🔗 Open Original** | Opens original job URL in new tab |
| **✉️ Test Email** | Sends a test email to verify SMTP config |

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
├── mailer.py               # Gmail SMTP sender (test email + application emails)
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
│   ├── indeed.py           # Indeed HK (Cloudflare retry + keyword filtering)
│   ├── efc.py              # eFinancialCareers (infinite scroll with smart stop)
│   └── manual.py           # Manual company list scraper
│
├── cookies/                 # Saved browser cookies (gitignored)
├── data/                    # Job detail JSON cache + SQLite DB (gitignored)
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
  Run → Scrape 5 platforms
  → Strict WIE filter (8 rules)
  → Discard ineligible jobs
  → Dedup + Save WIE-eligible only

Step 3: Batch Match (optional, N× LLM)
  ["🤖 Analyze All" button]
  → Match ALL unevaluated jobs against CV
  → Skip already-matched jobs
  → Real-time progress: "45/150 — Company Name..."

Step 4: Review (user-driven)
  ["🤖 Match Overview"] → Table view of all matches
  → Select job → View details
  → ["🤖 AI Analyze"] (1× LLM, cached after first run)

Step 5: Apply (manual)
  ["📝 Generate CL"] → AI cover letter (reuses cached job detail)
  → Review & edit in UI
  → ["📧 Apply"] → Email preview modal → Send via Gmail SMTP
```

### Smart Caching

Both **AI Analyze** and **Generate CL** reuse `data/job_details/{job_id}.json` cache:
- First time: re-opens job URL, extracts full page via LLM → saves to cache
- Subsequent times: reads directly from cache → **no re-fetching, no duplicate LLM calls**

**Analyze All** is its own caching layer: runs `_evaluate_cv_match()` per job, stores result in SQLite `jobs.cv_match` (survives cache deletion), skips already-evaluated jobs.

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
| python-multipart | >= 0.0.9 | FastAPI Form data support |

---

## 📝 Changelog

### 2026-06-21

#### Added
- **🤖 Analyze All** — Batch LLM match for all unevaluated jobs with real-time progress indicator; skips already-matched jobs
- **📊 Match Overview** — Sortable table: ✅/❌/⏳ match status, scores, and mismatch reasons for all jobs; click to jump to any job
- **✅/❌ per job in list** — `cv_match` result shown as icon next to each job in the dropdown

#### Fixed
- **Settings Save not updating config** — `Config.reload_inplace()` now calls `load_dotenv(override=True)` + re-reads `config.yaml`; previously saved credentials were silently ignored
- **`python-multipart` missing** — Added to `requirements.txt`; fresh clones now install correctly
- **LinkedIn Google login** — `linkedin_login.py` now uses `wait_for_selector` for Google OAuth iframe instead of blind `sleep(5)`

#### Removed
- **PolyU Job Board scraper** — PolyU scraper module and related config fields removed (PolyU login UI button kept for reference)

### 2026-06-19

#### Added
- **Smart Job Detail Cache** — `fetch_job_detail()` now caches to `data/job_details/{job_id}.json`; AI Writer and AI Evaluate both reuse the cache
- **Test Email Function** — Settings panel now has a "Test Email" section for SMTP verification
- **Stealth Module** — Added `stealth.py` for Playwright anti-detection (used by all scrapers)

#### Fixed
- **eFC scraper** — Fixed `seen_hrefs` global pollution bug; upgraded infinite scroll to human-like scrolling with smart stop
- **Indeed Cloudflare** — Added retry logic (detect challenge, wait 15s, retry up to 2×)
- **Indeed keyword filtering** — Added client-side title filtering for recommended jobs

---

## 📝 License

MIT
