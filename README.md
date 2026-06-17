# 🎯 WIE Internship Hunter v4

> **Automated internship hunting for PolyU WIE students** — scrape 6 platforms, AI-powered job detail extraction, and email applications, all from a clean web UI.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![FastAPI](https://img.shields.io/badge/Web%20UI-FastAPI-green.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

---

## 📖 What This Does

Finding a WIE (Work-Integrated Education) internship at PolyU is tedious — you need to scrape multiple job boards, check each one for CS eligibility & HK location requirements, research company details, and track your applications. **This tool automates the entire pipeline:**

```
Scrape Jobs → WIE Filter → CV Match → AI Detail Fetch → Track & Send
```

| Step | What Happens |
|------|-------------|
| 🔍 **Scrape** | Pulls jobs from 6 platforms simultaneously |
| 🎯 **Filter** | Checks WIE eligibility (CS role, HK location, not final-year-only) |
| 🤖 **Match** | Compares job requirements against your CV using LLM |
| 🌐 **Fetch Detail** | Opens job page in browser, extracts full description via LLM |
| ✍️ **Apply** | Review and send applications via Gmail SMTP |
| 📊 **Track** | Records everything in SQLite — no duplicate applications |

---

## ✨ Features

- **6 Job Sources** — LinkedIn, JobsDB, Indeed HK, eFinancialCareers, PolyU Job Board, Manual company list
- **🎯 On-Demand LLM** — CV parsing, CV-job matching, and detail extraction triggered manually from the UI; pipeline itself makes 0 LLM calls
- **🔐 LinkedIn Cookie Login** — One-click browser login saves cookies, bypassing Cloudflare detection
- **🎮 CV-Generated Keywords** — One-click button extracts search keywords from your CV and fills the keyword input
- **📄 Job Detail Panel** — Select a job, fetch full description + structured application info via LLM
- **📝 AI Cover Letters** — DeepSeek / OpenAI compatible API; generates role-specific, personalized cover letters
- **✉️ Manual Email Send** — Review and edit cover letter, then send (or dry-run) one at a time. No auto batch-send.
- **🌐 FastAPI Web UI** — Clean native HTML/JS interface: configure, run pipeline, review jobs, send emails — all in one page
- **⚙️ Built-in Config Panel** — Edit all settings (.env and config.yaml) directly in the UI
- **📤 CV Upload** — Drag-and-drop CV PDF upload in the UI; auto-saves to project directory
- **📊 SQLite Database** — Persistent job history, duplicate detection, cover letter storage, CV-match results, send history
- **📝 Live Log** — Real-time log viewer in the UI during pipeline execution
- **🚦 Progress Bar** — Visual pipeline phase indicator (Init → Scraping → Processing → Done)
- **🔒 Security First** — Default `.env` and `config.yaml` are gitignored; never commit real credentials

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Playwright** (Chromium) — for LinkedIn / detail fetching
- **Gmail account** with [App Password](https://support.google.com/accounts/answer/185833) — for sending emails
- **Optional:** PolyU NetID — for internal PolyU job board access
- **Optional:** DeepSeek or OpenAI API key — for AI cover letters

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
# Fill in credentials in the ⚙️ Settings panel
```

### Windows One-Click Launch

Double-click `run.bat` — it will auto-create venv, install deps, kill old processes, and launch the UI + auto-open browser.

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

# PolyU Jobboard (optional, for PolyU students)
POLYU_NET_ID=your_net_id
POLYU_PASSWORD=your_polyu_password
```

> **Note:** `.env` is gitignored. The repo ships without real credentials.

### `config.yaml` — Settings

All settings editable in the **⚙️ Settings** panel. Defaults are pre-filled — adjust keywords, scrapers, and filters to your needs.

| Section | Key Settings |
|---------|-------------|
| **CV** | `cv_pdf_path` — upload via UI or set path here |
| **Search** | `search_keywords` — list of search queries |
| **Scrapers** | Toggle individual platforms on/off |
| **WIE Filter** | HK location, CS role, final-year exclusions |
| **CV Matching** | LLM-based education & skills matching |
| **Cover Letter** | Language (en/zh), enable/disable |
| **Email** | Subject template, CV attachment, send delay |

---

## 🖥️ UI Overview

The web UI is a single-page interface with the following layout:

```
┌─────────────────────────────────────────────────────┐
│  [Logo] WIE Internship Hunter v4    ⚙️ Settings   │
├──────────────────────┬──────────────────────────────┤
│  Job List (left)     │  Job Detail (right)          │
│  ┌────────────────┐  │  ┌────────────────────────┐ │
│  │ Dropdown select │  │  │ Title, Company, URL    │ │
│  │ Job rows       │  │  │ AI Extracted Detail     │ │
│  │ [WIE?] [CV♟] │  │  │ Description             │ │
│  └────────────────┘  │  └────────────────────────┘ │
├──────────────────────┴──────────────────────────────┤
│  Control Panel                                      │
│  [Keywords...] [♟ CV Keywords] [🔐 LinkedIn Login] │
│  [✓ LinkedIn] [✓ JobsDB] [✓ Indeed] [✓ eFC]    │
│  [✓ PolyU] [Manual]                               │
│  [▶ Run] [⏹ Stop]                                │
├─────────────────────────────────────────────────────┤
│  Live Log (collapsible)                           │
└─────────────────────────────────────────────────────┘
```

### Control Panel

- **Search Keywords** — Comma-separated, editable inline
- **Scraper Toggles** — Enable/disable individual platforms (LinkedIn, JobsDB, Indeed, eFC, PolyU, Manual)
- **🔐 LinkedIn Login** — Opens a browser window for manual LinkedIn login; saves cookies for future scrapes (bypasses Cloudflare)
- **🎮 CV Keywords** — Extracts search keywords from your uploaded CV via LLM
- **▶ Run / ⏹ Stop** — Start/stop the scraping pipeline
- **📄 Fetch Detail** — Fetch full job description via LLM (per-job, on demand)
- **✉️ Send Email** — Send application email with CV attachment (per-job, manual)

---

## 🏗️ Architecture

```
internship-hunter/
├── web_ui.py               # FastAPI web UI (main entry point)
├── hunter.py                # Core pipeline: scrape -> rule-based filter (no LLM)
├── config.py                # Config loader (.env + config.yaml)
├── database.py              # SQLite ORM (jobs, cover_letters, history, seen_jobs)
├── models.py                # Job dataclass
├── ai_writer.py            # LLM cover letter generator (OpenAI-compatible API)
├── mailer.py               # Gmail SMTP sender with CV attachment
├── cv_reader.py            # CV PDF text extraction + LLM keyword extraction
├── fetch_job_detail.py     # Open job URL in browser, extract full detail via LLM
├── linkedin_login.py       # Standalone script: manual LinkedIn login, saves cookies
├── manual_companies.json   # Custom company list for manual scraping
│
├── scrapers/               # External job board scrapers
│   ├── __init__.py
│   ├── base.py             # Base scraper with Playwright page init + cookie loading
│   ├── linkedin.py         # LinkedIn job search (uses saved cookies)
│   ├── jobsdb.py           # JobsDB (Hong Kong)
│   ├── indeed.py           # Indeed HK
│   ├── efc.py              # eFinancialCareers
│   ├── polyu.py            # PolyU SAO Job Board (NetID login)
│   └── manual.py           # Manual company list scraper
│
├── cookies/                 # Saved browser cookies (gitignored)
├── data/                    # Job detail JSON cache (gitignored)
├── .env                     # Credentials (gitignored, template in repo)
├── config.yaml              # Settings (gitignored, template in repo)
├── requirements.txt         # Python dependencies
├── run.bat                 # Windows one-click launcher
└── README.md
```

### Pipeline Flow (lightweight — minimize LLM calls)

```
       ┌────────────────────────────────────┐
       │  Step 1: CV Parsing (UI, 1x LLM)│
       │  ["🎮 CV Keywords" button]         │
       │  -> Extract {technical, domains,   │
       │     roles} from CV                 │
       │  -> Fill keyword input             │
       └──────────────┬─────────────────────┘
                      ▼
       ┌────────────────────────────────────┐
       │  Step 2: Scrape (pipeline, 0x LLM)│
       │  Run -> Scrape 6 platforms         │
       │  -> Rule-based WIE filter          │
       │  -> Save to SQLite                 │
       └──────────────┬─────────────────────┘
                      ▼
       ┌────────────────────────────────────┐
       │  Step 3: Review (user-driven)     │
       │  Select job -> View details        │
       │  -> ["📄 Fetch Detail"] (1x LLM) │
       │  -> View AI-extracted job info     │
       └──────────────┬─────────────────────┘
                      ▼
       ┌────────────────────────────────────┐
       │  Step 4: Apply (manual)          │
       │  ["✉️ Send Email"]                │
       │  -> Generate cover letter (1x LLM) │
       │  -> Review & send (or dry-run)     │
       └────────────────────────────────────┘
```

### Data Flow

```
       CV PDF --> cv_reader.py --> cached keywords
                                       │
                                       ▼
   config keywords + CV keywords --> hunter.py (subprocess)
                                       │
                                       ▼
   Scrapers (6 sources) --> raw jobs --> rule-based WIE filter
                                                │
                                                ▼
                                         SQLite (jobs table)
                                                │
                          ┌─────────────────────┼─────────────────────┐
                          ▼                     ▼                     ▼
                    📄 Job Detail         ✉️ Email Panel      📊 History Tab
                    (manual LLM calls)   (manual send)       (application log)
```

---

## 🔐 LinkedIn Cloudflare Bypass

LinkedIn has strong Cloudflare protection that blocks automated scrapers. This project solves it by:

1. **Cookie-Based Auth** — Use the "🔐 LinkedIn Login" button in the UI to manually log in to LinkedIn (including passing any Cloudflare challenge)
3. **Cookie Persistence** — After login, cookies are saved to `cookies/linkedin.json`
4. **Automatic Cookie Loading** — Future scraping runs automatically load saved cookies, bypassing Cloudflare

> **Note:** Cookies expire after some time. If scraping fails, re-run "🔐 LinkedIn Login".

---

## 📋 Requirements

| Dependency | Version | Purpose |
|-----------|---------|---------|
| Python | >= 3.10 | Runtime |
| fastapi | >= 0.100 | Web UI backend |
| uvicorn | >= 0.20 | ASGI server |
| playwright | >= 1.44 | LinkedIn + on-demand job detail fetching |
| openai | >= 1.0 | LLM API client (DeepSeek/OpenAI) |
| requests | >= 2.31 | HTTP client (scrapers) |
| beautifulsoup4 | >= 4.12 | HTML parsing |
| PyYAML | >= 6.0 | Config file parsing |
| python-dotenv | >= 1.0 | Environment variable loading |
| PyPDF2 | >= 3.0 | CV PDF text extraction |

---

## 📝 License

MIT — feel free to use, modify, and share. Good luck with your WIE placement! 🎓

---

## 🙏 Acknowledgments

- [Playwright](https://playwright.dev/) for reliable browser automation
- [FastAPI](https://fastapi.tiangolo.com/) for the lightweight web framework
- [DeepSeek](https://www.deepseek.com/) for affordable LLM API access
- PolyU SAO for providing the internship job board
