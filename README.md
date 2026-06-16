# WIE Internship Hunter v4 🎯

**Automated internship hunting for PolyU WIE students.**

Scrapes 6 sources → AI cover letters → Email sending — all from a single web UI.

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## ✨ Features

- 🔍 **6 Job Sources**: PolyU Jobboard (SSO), LinkedIn, JobsDB, Indeed HK, eFinancialCareers, Manual company list
- 🤖 **AI Cover Letters**: DeepSeek/OpenAI-powered personalized cover letters based on job descriptions
- 🎛️ **Web UI**: Gradio single-page interface — configure, run, review, send all in one place
- 🧪 **Dry Run Mode**: Test everything without sending real emails
- 📊 **SQLite Database**: Job history, duplicate detection, application tracking
- 🍪 **Cookie Persistence**: PolyU Jobboard login once, reuse session
- 📧 **Gmail SMTP**: Attach CV automatically, rate-limited sending

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/VoidGod88/internship-hunter.git
cd internship-hunter

# 2. Install dependencies
pip install -r requirements.txt
python -m playwright install chromium

# 3. Configure
cp .env.example .env          # Edit with your credentials
cp config.example.yaml config.yaml  # Edit with your settings

# 4. Run CLI
python hunter.py

# 5. Or launch web UI
python app.py
# Open http://localhost:7860
```

## ⚙️ Configuration

### `.env` (secrets — never commit!)
```
EMAIL=your@gmail.com
EMAIL_PASSWORD=your_gmail_app_password
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
POLYU_NET_ID=your_netid
POLYU_PASSWORD=your_password
```

### `config.yaml` (settings — safe to share as template)
- Search keywords
- Scraper toggles
- WIE filtering rules
- Email settings

See `config.example.yaml` for full documentation.

## 📋 Requirements

- Python 3.10+
- Playwright (Chromium)
- Gmail account with App Password (for SMTP)
- Optional: PolyU NetID (for internal job board)
- Optional: DeepSeek/OpenAI API key (for AI cover letters)

## 🏗️ Architecture

```
internship_hunter/
├── app.py           # Gradio web UI
├── hunter.py        # Core pipeline orchestration
├── config.py        # Config loader (.env + config.yaml)
├── database.py      # SQLite ORM
├── models.py        # Job dataclass
├── jobboard.py      # PolyU Jobboard Playwright scraper
├── ai_writer.py     # LLM cover letter generator
├── mailer.py        # Gmail SMTP sender
├── scrapers/        # External job scrapers
│   ├── linkedin.py
│   ├── jobsdb.py
│   ├── indeed.py
│   ├── efc.py
│   └── manual.py
├── config.example.yaml
├── .env.example
└── manual_companies.json
```

## 🔒 Security

- `.env` and `config.yaml` are gitignored
- Never commit API keys or passwords
- Use Gmail App Passwords (not your main password)
- Enable 2FA on your Google account

## 📝 License

MIT — feel free to use and modify. Good luck with your WIE placement! 🎓

---

Built by [Yip Fung Ming](https://github.com/VoidGod88) | PolyU CS | Summer 2026
