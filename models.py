"""
models.py — Shared data models for the internship hunter.
"""
from dataclasses import dataclass, field


@dataclass
class Job:
    title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    source: str = ""
    posted_date: str = ""
    description: str = ""
    contact_email: str = ""
    requirements: str = ""
    education_level: str = ""
    is_final_year: str = ""
    wie_eligible: bool = False
    wie_reason: str = ""
    ai_relevance_score: int = 0
    status: str = "New"
    applied_date: str = ""
    extra_docs: str = ""  # Additional documents needed (transcript, form, etc.)
    notes: str = ""
    cover_letter_sent: str = ""
    _db_id: int = 0  # Runtime DB id, set after insert_job()

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "source": self.source,
            "posted_date": self.posted_date,
            "description": self.description,
            "contact_email": self.contact_email,
            "requirements": self.requirements,
            "education_level": self.education_level,
            "is_final_year": self.is_final_year,
            "wie_eligible": self.wie_eligible,
            "wie_reason": self.wie_reason,
            "ai_relevance_score": self.ai_relevance_score,
            "status": self.status,
            "extra_docs": self.extra_docs,
        }
