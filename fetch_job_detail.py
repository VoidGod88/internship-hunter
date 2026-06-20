"""
fetch_job_detail.py — Open a job URL, aggressively extract clean job description,
and ask LLM to extract structured fields.

Key improvement: uses multiple strategies to find the REAL job content
and strip all boilerplate (nav, footer, sidebar, cookie banners, etc.)
"""
import json
import logging
import re
from typing import Optional

from openai import OpenAI

from config import config

log = logging.getLogger("hunter")

JOB_DETAIL_EXTRACT_PROMPT = """You are analyzing a job posting. Extract structured information in detail.

JOB POSTING TEXT:
{description}

Return ONLY a valid JSON object with these fields:
{{
  "summary": "4-6 bullet points summarizing the role in detail (each bullet on a new line, use \\n). Each bullet should be 15-30 words, describing responsibilities, tools used, team context, or impact.",
  "requirements": ["requirement 1 (be specific: e.g. 'Python 3+ years' not just 'Python')", ...],
  "application_method": "How to apply. If email: state the email address AND what to attach (CV, transcript, portfolio, etc.). If portal: provide the full URL.",
  "application_materials": "Comma-separated list of materials to prepare/preview for application (e.g. 'CV, transcript, cover letter, portfolio link'). Infer from JD or common practice for this job type. Empty string if not mentioned and unclear.",
  "deadline": "Application deadline if mentioned, else empty string",
  "salary": "Salary/monthly rate if mentioned, else empty string",
  "work_type": "internship / full-time / part-time / contract",
  "location": "Work location if mentioned (city, district, remote, hybrid, on-site, etc)",
  "benefits": "Benefits / perks mentioned (e.g. transport allowance, meal subsidy, remote days, year-end bonus, insurance), else empty string",
  "start_date": "Expected start date if mentioned (e.g. July 2026, Immediate, January 2027), else empty string",
  "duration": "Internship duration if mentioned (e.g. 3 months, 6 weeks, 1 year), else empty string",
  "language_requirement": "Language requirement if mentioned (e.g. 'Fluent English required, Cantonese preferred'), else empty string",
  "visa_sponsorship": "Does the company sponsor visa? \"true\" / \"false\" / \"unclear\" (infer from JD, e.g. 'non-local welcome' means true, 'HKID only' means false)"
}}

Rules:
- summary: write detailed bullets (15-30 words each), covering what the candidate will do, what tools/tech they'll use, and what impact they'll have.
- requirements: extract BOTH hard requirements AND preferred skills. Be specific (version numbers, years of experience, certifications).
- application_method: if email, MUST include what to attach (e.g. "Email: hr@company.com with CV, transcript, and cover letter").
- application_materials: list ONLY the materials, not the email address (e.g. "CV, transcript, cover letter, GitHub portfolio").
- benefits / start_date / duration / language_requirement: fill if clearly mentioned, else infer from context or leave empty.
- visa_sponsorship: "true" only if JD explicitly mentions visa sponsorship or welcomes non-local students. "false" if HKID/PR required. "unclear" if not mentioned.
- Return ONLY the JSON object, no markdown, no explanation.
"""

# Boilerplate selectors to REMOVE before extracting text
REMOVE_SELECTORS = [
    "nav", "header", "footer",
    "[role='navigation']", "[role='banner']", "[role='contentinfo']",
    ".nav", ".navbar", ".navigation", ".menu", ".sidebar",
    ".header", ".footer", ".cookie-banner", ".cookie-notice", ".cookie-consent",
    "[class*='cookie']", "[class*='banner']", "[class*='popup']", "[class*='modal']",
    "[class*='advertisement']", "[class*='social']", "[class*='share']",
    ".social-media", ".share-buttons", ".related-posts", ".comments",
    "script", "style", "noscript", "iframe",
]

# Job content selectors (priority order)
JOB_CONTENT_SELECTORS = [
    # LinkedIn
    ".jobs-description", "[class*='jobs-description']", "[class*='job-description']",
    # Generic job sites
    "[class*='job-description']", "[class*='jobDescription']", "[class*='job_description']",
    "[class*='description']", "[class*='posting']", "[class*='vacancy']",
    "[class*='position-details']", "[class*='positionDetails']",
    # WordPress / generic
    ".entry-content", ".post-content", ".content-area", "#content",
    "article", "main", "[role='main']",
]


def _aggressive_clean(page) -> str:
    """Remove ALL boilerplate from the page using JavaScript."""
    try:
        page.evaluate("""() => {
            const REMOVE = [
                'nav', 'header', 'footer', 'aside',
                '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                '.nav', '.navbar', '.navigation', '.menu', '.sidebar',
                '.header', '.footer', '.cookie-banner', '.cookie-notice', '.cookie-consent',
                '[class*="cookie"]', '[class*="banner"]', '[class*="popup"]', '[class*="modal"]',
                '[class*="advertisement"]', '[class*="social"]', '[class*="share"]',
                '.social-media', '.share-buttons', '.related-posts', '.comments-section',
                'script', 'style', 'noscript', 'iframe',
                '[id*="cookie"]', '[id*="banner"]', '[id*="popup"]',
                '.gdpr', '.privacy-banner', '[class*="gdpr"]', '[class*="privacy"]',
            ];
            REMOVE.forEach(sel => {
                try {
                    document.querySelectorAll(sel).forEach(el => {
                        if (el && el.parentNode) el.remove();
                    });
                } catch(e) {}
            });

            // Also remove elements that are clearly not job content by their text content
            document.querySelectorAll('a').forEach(a => {
                const t = a.innerText || '';
                if (t.length > 0 && t.length < 30 && (
                    t.includes('LinkedIn') || t.includes('Facebook') || t.includes('Twitter') ||
                    t.includes('Instagram') || t.includes('WhatsApp') || t.includes('Share') ||
                    t.includes('Follow') || t.includes('Subscribe')
                )) {
                    // Don't remove, just mark - actually, keep links
                }
            });
        }""")
    except Exception as e:
        log.warning(f"[_aggressive_clean] JS remove failed: {e}")

    # Strategy 1: Try explicit job content selectors
    for sel in JOB_CONTENT_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                if len(text) > 200:
                    return text
        except Exception:
            continue

    # Strategy 2: Find the <div> or <section> with the most text content
    # that isn't a nav/footer/sidebar
    try:
        best_text = ""
        best_len = 0
        for tag in ["section", "div", "article", "main"]:
            elements = page.query_selector_all(tag)
            for el in elements:
                try:
                    # Skip if element has nav/footer/menu in its class or id
                    cls = (el.get_attribute("class") or "").lower()
                    id_attr = (el.get_attribute("id") or "").lower()
                    if any(x in cls or x in id_attr for x in [
                        "nav", "menu", "sidebar", "footer", "header", "cookie", "banner"
                    ]):
                        continue

                    t = el.inner_text().strip()
                    # Heuristic: good content block has reasonable length
                    # and is not tiny, and has multiple children (real content)
                    children = el.query_selector_all("*")
                    if 100 < len(t) < 50000 and len(children) > 5:
                        if len(t) > best_len:
                            best_text = t
                            best_len = len(t)
                except Exception:
                    continue
        if best_text:
            return best_text
    except Exception as e:
        log.warning(f"[_aggressive_clean] Best-element strategy failed: {e}")

    # Strategy 3: Fallback — body text after JS removal
    try:
        body = page.query_selector("body")
        if body:
            return body.inner_text().strip()
    except Exception:
        pass

    return ""


def _fetch_page_text(url: str, timeout_ms: int = 20_000) -> str:
    """Open URL with Playwright, aggressively clean, return text."""
    from pathlib import Path
    from playwright.sync_api import sync_playwright

    # Detect if this is a PolyU URL that needs cookies
    is_polyu = "polyu.edu.hk" in url.lower()
    cookie_path = Path(__file__).parent / "cookies" / "polyu.json"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()

            # Set stealth headers
            page.set_extra_http_headers({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8",
            })

            # Load cookies for PolyU pages
            if is_polyu and cookie_path.exists():
                try:
                    ctx = browser.new_context()
                    ctx.add_cookies(json.loads(cookie_path.read_text(encoding="utf-8")))
                    page.close()
                    page = ctx.new_page()
                    page.set_extra_http_headers({
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                        "Accept-Language": "en-US,en;q=0.9,zh-HK;q=0.8",
                    })
                    log.info(f"[fetch_detail] Loaded PolyU cookies from {cookie_path}")
                except Exception as e:
                    log.warning(f"[fetch_detail] Failed to load PolyU cookies: {e}")

            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)  # Wait for JS to render

            text = _aggressive_clean(page)

        except Exception as e:
            log.warning(f"[_fetch_page_text] Playwright error: {e}")
            text = ""
        finally:
            browser.close()

    if not text:
        return ""

    # Post-process: clean up the extracted text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Remove lines that look like navigation/menu items
    # (very short, all caps, or contain navigation keywords)
    NAV_KEYWORDS = [
        "home", "about", "contact", "login", "sign up", "register",
        "menu", "search", "cart", "account", "profile",
        "facebook", "twitter", "linkedin", "instagram", "youtube",
        "cookie", "privacy", "terms", "sitemap", "accessibility",
        "©", "copyright", "all rights reserved",
        "follow us", "share", "subscribe", "newsletter",
    ]

    cleaned = []
    prev_empty = False
    for ln in lines:
        lower = ln.lower()

        # Skip navigation-like lines
        if len(ln) < 4:
            continue
        if any(kw in lower for kw in NAV_KEYWORDS) and len(ln) < 50:
            continue
        # Skip lines that are just URLs (unless they're the job URL)
        if re.match(r'^https?://[^\s]+$', ln) and len(ln) < 100:
            continue
        # Skip repeated empty lines
        if ln == "" and prev_empty:
            continue

        cleaned.append(ln)
        prev_empty = (ln == "")

    text = "\n".join(cleaned)

    # Collapse 3+ blank lines into 1
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _call_llm_extract(description: str) -> Optional[dict]:
    """Ask LLM to extract structured fields from the description text."""
    if not config.llm_api_key:
        log.warning("[Fetch Detail] No LLM API key")
        return None
    if not description or len(description) < 80:
        log.warning("[Fetch Detail] Description too short for LLM")
        return None

    try:
        client = OpenAI(api_key=config.llm_api_key, base_url=config.llm_base_url)
        truncated = description[:10000]

        response = client.chat.completions.create(
            model=config.llm_model,
            messages=[
                {"role": "system", "content": (
                    "You are a job posting analyzer. "
                    "Given the text content of a job posting webpage (with navigation/footer already removed), "
                    "extract structured information. Return only valid JSON."
                )},
                {"role": "user", "content": JOB_DETAIL_EXTRACT_PROMPT.format(description=truncated)},
            ],
            temperature=0.2,
            max_tokens=1000,
        )

        content = (response.choices[0].message.content or "").strip()

        # Strip markdown code blocks
        if "```" in content:
            parts = content.split("```")
            for i in range(1, len(parts), 2):
                candidate = parts[i].strip()
                if candidate:
                    candidate = re.sub(r'^(?:json|JSON)\s*', '', candidate).strip()
                    if candidate:
                        content = candidate
                        break
            else:
                content = content.replace("```", "").strip()

        # Extract first {...}
        json_match = re.search(r'\{[\s\S]*?\}', content)
        if json_match:
            content = json_match.group(0).strip()

        result = json.loads(content)
        if isinstance(result, dict):
            return result
        return None
    except json.JSONDecodeError as e:
        log.warning(f"[Fetch Detail] LLM returned invalid JSON: {e} | content={content[:200]}")
        return None
    except Exception as e:
        log.warning(f"[Fetch Detail] LLM extract failed: {type(e).__name__}: {e}")
        return None


def _fetch_page_text_cloudscraper(url: str) -> str:
    """Fallback: use cloudscraper to bypass Cloudflare (no JS rendering)."""
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=15)
        if resp.status_code != 200:
            return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove boilerplate tags
        for tag in REMOVE_SELECTORS:
            for el in soup.select(tag):
                el.decompose()

        # Try job content selectors
        for sel in JOB_CONTENT_SELECTORS:
            el = soup.select_one(sel)
            if el:
                text = el.get_text("\n", strip=True)
                if len(text) > 200:
                    return text

        # Fallback: body text
        body = soup.find("body")
        if body:
            return body.get_text("\n", strip=True)
        return resp.text[:10000]
    except Exception as e:
        log.warning(f"[_fetch_page_text_cloudscraper] Failed: {e}")
        return ""


def fetch_job_detail(url: str) -> dict:
    """
    Open the URL, grab clean description, ask LLM to extract structure.
    Returns dict with keys:
        - description: clean page text (always present, even on failure)
        - structured: dict from LLM (None if LLM failed)
        - error: error string (None on success)
    """
    result = {
        "description": "",
        "structured": None,
        "error": None,
    }

    if not url:
        result["error"] = "No URL provided"
        return result

    try:
        text = _fetch_page_text(url, timeout_ms=20_000)
        result["description"] = text
    except Exception as e:
        log.warning(f"[fetch_job_detail] Playwright failed: {e}, trying cloudscraper...")
        result["error"] = f"Playwright failed: {e}"

    # Fallback: cloudscraper (no browser, bypasses Cloudflare for static pages)
    if not result.get("description") or len(result.get("description", "")) < 80:
        fb = _fetch_page_text_cloudscraper(url)
        if fb and len(fb) > 80:
            result["description"] = fb
            result["error"] = None  # cleared if cloudscraper succeeded
            log.info("[fetch_job_detail] cloudscraper fallback succeeded")

    text = result.get("description", "")
    if not text or len(text) < 80:
        if not result.get("error"):
            result["error"] = "Page text too short or empty"
        return result

    structured = _call_llm_extract(text)
    result["structured"] = structured
    return result


JOB_ANALYZE_PROMPT = """You are a thorough job posting analyzer and CV evaluator.

Given the CV profile (JSON) and the FULL job posting text (scraped from the URL), extract ALL of the following fields in ONE JSON object.

## CV Profile:
{cv_profile}

## Job Posting Text:
{job_text}

Return ONLY a valid JSON object with ALL these keys:

### Detail fields (from job posting):
- "summary": string — 4-6 detailed bullet points describing the role (each bullet on a new line, use \\n). Each bullet should be 15-30 words, covering: what the candidate will do, what tools/tech they'll use, team context, and expected impact.
- "requirements": array of strings — BOTH hard requirements AND preferred skills. Be specific: include version numbers, years of experience, certifications, and languages. Each item 10-25 words.
- "application_method": string — How to apply. If email: state the email address AND what to attach (e.g. "Email: hr@company.com with CV, transcript, and cover letter"). If portal: provide the full URL.
- "application_materials": string — Comma-separated list of materials to prepare for application (e.g. "CV, transcript, cover letter, GitHub portfolio, academic reference"). Infer from JD requirements OR common practice for this job type. Empty string only if completely unclear.
- "deadline": string — Application deadline if mentioned, else empty string
- "salary": string — Salary/monthly rate if mentioned, else empty string
- "work_type": string — "internship" / "full-time" / "part-time" / "contract"
- "location": string — Work location (city, district, remote, hybrid, on-site, etc)
- "benefits": string — Benefits / perks mentioned in detail (e.g. "Transport allowance HKD 1,000/month, meal subsidy, 12 annual leaves, medical insurance, remote 2 days/week"), else empty string
- "start_date": string — Expected start date if mentioned (e.g. July 2026, Immediate, January 2027), else empty string
- "duration": string — Internship duration if mentioned (e.g. 3 months, 6 weeks, 1 year), else empty string
- "language_requirement": string — Language requirement in detail (e.g. "Fluent English required, Cantonese preferred, Mandarin a plus"), else empty string
- "visa_sponsorship": string — "true" if JD mentions visa sponsorship / non-local welcome, "false" if explicitly HKID/PR only, else "unclear"

### Match fields (compare CV vs job):
- "overall_match": bool — true if candidate is a good fit overall
- "skills_match": bool — Does the candidate have the required skills?
- "education_match": bool — Does the candidate meet the education requirement?
- "major_match": bool — Does the candidate's major match the job requirement?
- "experience_match": bool — Does the candidate have enough experience? (false if job requires work experience but candidate has none)
- "match_score": int — 0-100, how well the candidate matches
- "reasons": string — Explanation in Chinese (合格/不合格的理由，2-3句，具体说明哪些条件符合/不符合)
- "requires_final_year": bool — Does the job require final-year students?
- "candidate_is_final_year": bool — Set to false if unknown
- "requires_experience": bool — Does the job require prior work experience (NOT internship)?

Rules:
- summary: write DETAILED bullets (15-30 words each), covering: main responsibilities, tools/tech stack, team size/context, and business impact.
- requirements: extract BOTH hard requirements AND preferred skills. Be specific with numbers (e.g. "Python 3+ years" not "Python").
- application_method: if email, MUST include both the email address AND what materials to attach.
- application_materials: list ONLY the materials (not the email). Infer from JD or common practice (e.g. finance internship → CV, transcript, cover letter; design role → CV, portfolio, cover letter).
- benefits / start_date / duration / language_requirement: fill if clearly mentioned, else infer from context or leave empty.
- visa_sponsorship: "true" only if JD explicitly mentions visa or welcomes non-local. "false" if HKID/PR required. "unclear" if not mentioned.
- match_score: 0-100, be strict. Deduct for missing skills, wrong major, or visa issues.
- reasons: explain in 2-3 Chinese sentences, citing SPECIFIC conditions matched or missed.
- Return ONLY the JSON object, no markdown, no explanation.
"""


def analyze_job(cv_profile: dict, job: dict, cfg) -> dict:
    """
    Unified analyze: scrape full page -> LLM returns all 22 fields.
    Returns dict with keys: detail (dict with 12 fields) + match (dict with 10 fields)
    """
    url = job.get("url", "")
    if not url:
        return {"error": "No URL for this job"}

    # Step 1: fetch full page text
    log.info(f"[analyze_job] Fetching page: {url}")
    page_text = _fetch_page_text(url, timeout_ms=20_000)

    if not page_text or len(page_text) < 80:
        # Fallback: cloudscraper
        log.info("[analyze_job] Playwright failed, trying cloudscraper...")
        page_text = _fetch_page_text_cloudscraper(url)

    if not page_text or len(page_text) < 80:
        return {"error": "Failed to fetch page content"}

    # Step 2: call LLM with full page text + CV
    if not cfg.llm_api_key:
        return {"error": "No LLM API key configured"}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url or None)

        cv_text = json.dumps(cv_profile, ensure_ascii=False, indent=2)[:3000]
        job_title = job.get("title", "")
        job_company = job.get("company", "")
        # Truncate page_text to avoid token overflow
        truncated_text = page_text[:10000]

        prompt = JOB_ANALYZE_PROMPT.format(
            cv_profile=cv_text,
            job_text=f"Title: {job_title}\nCompany: {job_company}\n\n{truncated_text}"
        )

        log.info(f"[analyze_job] Calling LLM for job: {job_title}")
        resp = client.chat.completions.create(
            model=cfg.llm_model or "deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=2000,
        )

        content = (resp.choices[0].message.content or "").strip()

        # Strip markdown code blocks
        if "```" in content:
            parts = content.split("```")
            for i in range(1, len(parts), 2):
                candidate = parts[i].strip()
                if candidate:
                    candidate = re.sub(r'^(?:json|JSON)\s*', '', candidate).strip()
                    if candidate:
                        content = candidate
                        break
            else:
                content = content.replace("```", "").strip()

        # Extract first {...}
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            content = json_match.group(0).strip()

        result = json.loads(content)
        if isinstance(result, dict):
            result["description"] = page_text[:10000]
            log.info(f"[analyze_job] LLM returned {len(result)} fields")
            return result
        return {"error": "LLM returned non-dict JSON"}

    except json.JSONDecodeError as e:
        log.warning(f"[analyze_job] LLM returned invalid JSON: {e} | content={content[:300]}")
        return {"error": f"LLM JSON parse error: {e}"}
    except Exception as e:
        log.exception(f"[analyze_job] LLM call failed: {e}")
        return {"error": str(e)}
