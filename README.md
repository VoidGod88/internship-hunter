# 🎯 WIE Internship Hunter v4

> **Automated internship hunting for PolyU WIE students** — scrape 6 platforms, generate AI cover letters, and send applications, all from a single web UI.

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Gradio](https://img.shields.io/badge/Gradio-5.0+-orange.svg)](https://www.gradio.app/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)]()

---

## 📖 What This Does

Finding a WIE (Work-Integrated Education) internship at PolyU is tedious — you need to scrape multiple job boards, check each one for CS eligibility & HK location requirements, write custom cover letters, and track your applications. **This tool automates the entire pipeline:**

```
Scrape Jobs → WIE Filter → CV Match → AI Cover Letter → Track & Send
```

| Step | What Happens |
|------|-------------|
| 🔍 **Scrape** | Pulls jobs from 6 platforms simultaneously |
| 🎓 **Filter** | Checks WIE eligibility (CS role, HK location, not final-year-only) |
| 🤖 **Match** | Compares job requirements against your CV using LLM |
| ✍️ **Generate** | Creates personalized cover letters via DeepSeek/OpenAI |
| 📧 **Send** | Emails applications via Gmail SMTP with CV attachment |
| 📊 **Track** | Records everything in SQLite — no duplicate applications |

---

## ✨ Features

- **6 Job Sources** — PolyU Jobboard (SSO auto-login), LinkedIn, JobsDB, Indeed HK, eFinancialCareers, Manual company list
- **AI Cover Letters** — DeepSeek / OpenAI compatible API; generates role-specific, personalized cover letters
- **Web UI** — Full Gradio interface: configure, run pipeline, review letters, send emails — all in one page
- **⚙️ Built-in Config Panel** — Edit all settings (API keys, keywords, scrapers, WIE filters) directly in the UI — no file editing needed
- **📄 CV Upload** — Drag-and-drop CV PDF upload in the UI; auto-saves to project directory
- **🧪 Dry Run Mode** — Test the entire pipeline without sending real emails
- **🍪 Cookie Persistence** — Log into PolyU Jobboard once; session is saved for future runs
- **📊 SQLite Database** — Persistent job history, duplicate detection (per company+title+source), cover letter storage, send history
- **📝 Live Log** — Real-time log viewer in the UI during pipeline execution
- **🚦 Progress Bar** — Visual pipeline phase indicator (Init → Scraping → Processing → Generating → Done)
- **🔒 Security First** — `.env` and `config.yaml` are gitignored; credential templates provided

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Playwright** (Chromium) — for PolyU Jobboard scraping
- **Gmail account** with [App Password](https://support.google.com/accounts/answer/185833) — for sending emails
- **Optional:** PolyU NetID — for internal job board access
- **Optional:** DeepSeek or OpenAI API key — for AI cover letters

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/VoidGod88/internship-hunter.git
cd internship-hunter

# 2. Create virtual environment & install dependencies
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

pip install -r requirements.txt
python -m playwright install chromium

# 3. Configure credentials
cp .env.example .env
cp config.example.yaml config.yaml

# 4. Launch the web UI
python app.py
# Open http://localhost:7861 in your browser
```

### Windows One-Click Launch

Double-click `run.bat` — it will auto-create venv, install deps, and launch the UI.

---

## ⚙️ Configuration

### `.env` — Credentials (never commit!)

```env
EMAIL=your@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
LLM_PROVIDER=deepseek          # deepseek / openai / custom
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
POLYU_NET_ID=your_netid        # Optional: for PolyU Jobboard
POLYU_PASSWORD=your_password   # Optional: for PolyU Jobboard
```

### `config.yaml` — Settings

All settings can also be edited in the **⚙️ Config** tab in the web UI.

| Section | Key Settings |
|---------|-------------|
| **CV** | `cv_pdf_path` — path to your CV PDF |
| **Search** | `search_keywords` — list of search queries |
| **Scrapers** | Toggle individual platforms on/off |
| **WIE Filter** | HK location, CS role, final-year exclusions |
| **CV Matching** | LLM-based education & skills matching |
| **Cover Letter** | Language (en/zh), enable/disable |
| **Email** | Subject template, CV attachment, send delay |

See `config.example.yaml` for the full annotated template.

---

## 🖥️ UI Overview

The web UI has **7 tabs**:

| Tab | Purpose |
|-----|---------|
| 📋 **Jobs** | View scraped jobs with WIE status, CV match score, and reason |
| ✉️ **Cover Letter** | Preview/edit AI-generated letters, send or skip per job |
| 📊 **History** | Track all sent applications with timestamps |
| 📝 **Live Log** | Real-time `hunter.log` viewer during pipeline runs |
| ⚙️ **Config** | Edit all `.env` and `config.yaml` settings in the UI |
| 📖 **Help** | Quick reference guide |

**Pipeline Controls** (left panel):
- **Search Keywords** — Comma-separated, editable inline
- **Dry Run Toggle** — Test mode (no real emails sent)
- **Max Emails / Run** — Safety limit (1-50)
- **Scraper Toggles** — Enable/disable individual platforms
- **Cover Letter Toggle** — Enable/disable AI generation
- **RUN PIPELINE** / **STOP** buttons
- **Real-time status** with progress bar and log tail

---

## 🏗️ Architecture

```
internship-hunter/
├── app.py                  # Gradio web UI (~1300 lines)
├── hunter.py               # Core pipeline: scrape → filter → match → generate → send
├── config.py               # Config loader (.env + config.yaml)
├── database.py             # SQLite ORM (jobs, cover_letters, history, seen_jobs)
├── models.py               # Job dataclass
├── jobboard.py             # PolyU Jobboard Playwright scraper (SSO login)
├── ai_writer.py            # LLM cover letter generator (OpenAI-compatible API)
├── mailer.py               # Gmail SMTP sender with CV attachment
├── cv_reader.py            # CV PDF text extraction (PyPDF2)
├── manual_companies.json   # Custom company list for manual scraping
│
├── scrapers/               # External job board scrapers
│   ├── __init__.py
│   ├── base.py             # Abstract base scraper
│   ├── linkedin.py         # LinkedIn job search
│   ├── jobsdb.py           # JobsDB (Hong Kong)
│   ├── indeed.py           # Indeed HK
│   ├── efc.py              # eFinancialCareers
│   └── manual.py           # Manual company list scraper
│
├── config.example.yaml     # Configuration template (safe to share)
├── .env.example            # Credentials template (safe to share)
├── requirements.txt        # Python dependencies
├── run.bat                 # Windows one-click launcher
└── README.md
```

### Data Flow

```
                              ┌─────────────────┐
                              │   Gradio Web UI  │
                              │    (app.py)      │
                              └────────┬────────┘
                                       │ Runs subprocess
                              ┌────────▼────────┐
                              │  Pipeline        │
                              │  (hunter.py)     │
                              └───┬───┬───┬─────┘
                    ┌─────────────┘   │   └─────────────┐
              ┌─────▼─────┐   ┌──────▼──────┐   ┌──────▼──────┐
              │  Scrapers  │   │  AI Writer  │   │   Mailer    │
              │ (6 sources)│   │(DeepSeek/   │   │(Gmail SMTP) │
              │            │   │  OpenAI)    │   │             │
              └─────┬──────┘   └──────┬──────┘   └──────┬──────┘
                    │                 │                  │
                    └─────────┬───────┴──────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │     SQLite DB      │
                    │  (database.py)     │
                    └───────────────────┘
```

---

## 🔒 Security

- `.env` and `config.yaml` are **gitignored** — never commit credentials
- Use **Gmail App Passwords** (not your main password) — [setup guide](https://support.google.com/accounts/answer/185833)
- Enable **2FA** on your Google account
- `config.example.yaml` and `.env.example` are templates safe to share publicly
- Playwright cookies are stored locally in `cookies/` (gitignored)

---

## 📋 Requirements

| Dependency | Version | Purpose |
|-----------|---------|---------|
| Python | ≥ 3.10 | Runtime |
| gradio | ≥ 5.0 | Web UI |
| playwright | ≥ 1.44 | PolyU Jobboard browser automation |
| openai | ≥ 1.0 | LLM API client (DeepSeek/OpenAI) |
| requests | ≥ 2.31 | HTTP client (scrapers) |
| beautifulsoup4 | ≥ 4.12 | HTML parsing |
| openpyxl | ≥ 3.1 | Excel export (tracker) |
| PyYAML | ≥ 6.0 | Config file parsing |
| python-dotenv | ≥ 1.0 | Environment variable loading |
| PyPDF2 | ≥ 3.0 | CV PDF text extraction |

---

## 📝 License

MIT — feel free to use, modify, and share. Good luck with your WIE placement! 🎓

---

Built by **[Yip Fung Ming](https://github.com/VoidGod88)** | PolyU CS Year 1 | Summer 2026
