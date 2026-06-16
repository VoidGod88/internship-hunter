"""
database.py — SQLite persistence for jobs, cover letters, and application history.
"""

import sqlite3
import datetime
import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict

DB_PATH = Path(__file__).parent / "hunter.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            location TEXT DEFAULT '',
            url TEXT DEFAULT '',
            source TEXT DEFAULT '',
            posted_date TEXT DEFAULT '',
            description TEXT DEFAULT '',
            contact_email TEXT DEFAULT '',
            requirements TEXT DEFAULT '',
            education_level TEXT DEFAULT '',
            is_final_year TEXT DEFAULT '',
            wie_eligible INTEGER DEFAULT 0,
            wie_reason TEXT DEFAULT '',
            ai_relevance_score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'New',
            extra_docs TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS cover_letters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            draft_content TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS application_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            sent_at TEXT DEFAULT (datetime('now','localtime')),
            dry_run INTEGER DEFAULT 0,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS seen_jobs (
            key_hash TEXT PRIMARY KEY,
            first_seen TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
        CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_cover_letters_job_id ON cover_letters(job_id);
    """)
    
    # Migration: add extra_docs column if missing
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN extra_docs TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration v4→v5: seen_jobs key format changed from 2-part (company|title)
    # to 3-part (company|title|source). Truncate to avoid false duplicates.
    try:
        conn.execute("ALTER TABLE seen_jobs ADD COLUMN last_updated TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.execute("DELETE FROM seen_jobs")  # Rebuild on next run with new key format
    conn.execute("PRAGMA user_version = 5")
    
    conn.commit()
    conn.close()


def insert_job(job_data) -> int:
    """Insert a job and return its id.
    
    Dedup key: MD5(company|title|source). On duplicate, UPDATE all fields
    (full replace) and return the existing job_id.
    """
    conn = get_db()

    # Normalize to dict
    if hasattr(job_data, 'to_dict'):
        job_dict = job_data.to_dict()
    elif isinstance(job_data, dict):
        job_dict = job_data
    else:
        job_dict = asdict(job_data)

    key = f"{job_dict.get('company','')}|{job_dict.get('title','')}|{job_dict.get('source','')}"
    import hashlib
    key_hash = hashlib.md5(key.encode()).hexdigest()

    fields = [
        "title", "company", "location", "url", "source", "posted_date",
        "description", "contact_email", "requirements", "education_level",
        "is_final_year", "wie_eligible", "wie_reason", "ai_relevance_score", "status", "extra_docs"
    ]
    values = {f: job_dict.get(f, "") for f in fields}
    values["wie_eligible"] = 1 if values["wie_eligible"] else 0

    existing_hash = conn.execute(
        "SELECT 1 FROM seen_jobs WHERE key_hash=?", (key_hash,)
    ).fetchone()

    if existing_hash:
        # Find existing job row and UPDATE (full replace)
        existing_job = conn.execute(
            "SELECT id FROM jobs WHERE company=? AND title=? AND source=? LIMIT 1",
            (job_dict.get('company', ''), job_dict.get('title', ''), job_dict.get('source', ''))
        ).fetchone()
        if existing_job:
            jid = existing_job["id"]
            set_clause = ", ".join(f"{f}=?" for f in values.keys())
            conn.execute(
                f"UPDATE jobs SET {set_clause} WHERE id=?",
                list(values.values()) + [jid]
            )
            # Update last_updated timestamp on seen_jobs
            conn.execute(
                "UPDATE seen_jobs SET last_updated=datetime('now','localtime') WHERE key_hash=?",
                (key_hash,)
            )
            conn.commit()
            conn.close()
            return jid

    # Insert new
    cols = ", ".join(values.keys())
    placeholders = ", ".join("?" * len(values))
    cursor = conn.execute(
        f"INSERT INTO jobs ({cols}) VALUES ({placeholders})",
        list(values.values())
    )
    conn.execute("INSERT OR IGNORE INTO seen_jobs (key_hash) VALUES (?)", (key_hash,))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    return job_id


def insert_cover_letter(job_id: int, content: str) -> int:
    conn = get_db()
    # Upsert
    existing = conn.execute(
        "SELECT id FROM cover_letters WHERE job_id=?", (job_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE cover_letters SET content=?, draft_content=?, updated_at=datetime('now','localtime') WHERE job_id=?",
            (content, content, job_id)
        )
        cl_id = existing["id"]
    else:
        cursor = conn.execute(
            "INSERT INTO cover_letters (job_id, content, draft_content) VALUES (?, ?, ?)",
            (job_id, content, content)
        )
        cl_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return cl_id


def update_cover_letter_draft(job_id: int, draft: str):
    conn = get_db()
    conn.execute(
        "UPDATE cover_letters SET draft_content=? WHERE job_id=?",
        (draft, job_id)
    )
    conn.commit()
    conn.close()


def get_cover_letter(job_id: int) -> Optional[str]:
    """Get the cover letter content for a specific job."""
    conn = get_db()
    row = conn.execute(
        "SELECT content FROM cover_letters WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job_id,)
    ).fetchone()
    conn.close()
    return row["content"] if row else None


def record_application(job_id: int, dry_run: bool = False):
    conn = get_db()
    conn.execute(
        "INSERT INTO application_history (job_id, dry_run) VALUES (?, ?)",
        (job_id, 1 if dry_run else 0)
    )
    conn.execute(
        "UPDATE jobs SET status=? WHERE id=?",
        ("Applied (Dry Run)" if dry_run else "Applied", job_id)
    )
    conn.commit()
    conn.close()


def has_been_applied(company: str, title: str) -> bool:
    """Check if we've already applied to this company+title combo."""
    conn = get_db()
    row = conn.execute(
        """SELECT 1 FROM jobs j
           JOIN application_history ah ON j.id = ah.job_id
           WHERE j.company LIKE ? AND j.title LIKE ? AND ah.dry_run = 0
           LIMIT 1""",
        (f"%{company}%", f"%{title}%")
    ).fetchone()
    conn.close()
    return row is not None


def get_jobs_with_cover_letters(status: Optional[str] = None) -> list[dict]:
    conn = get_db()
    query = """
        SELECT j.*, cl.content as cover_letter, cl.draft_content as cover_letter_draft,
               cl.id as cover_letter_id, cl.status as cl_status
        FROM jobs j
        LEFT JOIN cover_letters cl ON j.id = cl.job_id
    """
    params = []
    if status:
        query += " WHERE j.status = ?"
        params.append(status)
    query += " ORDER BY j.wie_eligible DESC, j.ai_relevance_score DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_jobs() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY wie_eligible DESC, ai_relevance_score DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_application_history() -> list[dict]:
    conn = get_db()
    rows = conn.execute("""
        SELECT ah.*, j.company, j.title
        FROM application_history ah
        JOIN jobs j ON ah.job_id = j.id
        ORDER BY ah.sent_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_job_status(job_id: int, status: str):
    conn = get_db()
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    conn.commit()
    conn.close()


# ── Init on import ──
init_db()
