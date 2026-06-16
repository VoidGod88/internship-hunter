"""
scrapers/__init__.py — All job scrapers, share a single Playwright browser instance.
"""
from .linkedin import scrape_linkedin
from .jobsdb import scrape_jobsdb
from .indeed import scrape_indeed
from .efc import scrape_efc
from .manual import load_manual
from models import Job

__all__ = [
    "scrape_linkedin",
    "scrape_jobsdb", 
    "scrape_indeed",
    "scrape_efc",
    "load_manual",
    "Job",
]
