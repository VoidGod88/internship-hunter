"""
mailer.py — Gmail SMTP email sender for internship applications.
NOTE: Currently only _get_sender_name() is used by other modules.
The test-email endpoint lives in web_ui.py (no longer uses this module).
send_email() / send_batch() are disabled — will be rewritten when
the real "apply via email" feature is re-added.
"""

import logging
from config import config

log = logging.getLogger("hunter")

try:
    from cv_reader import load_cv_profile
    _has_cv_reader = True
except Exception:
    _has_cv_reader = False


def get_sender_name() -> str:
    """Get sender name from CV profile, or return placeholder."""
    if _has_cv_reader and config.cv_pdf_path:
        try:
            profile = load_cv_profile(config.cv_pdf_path)
            name = profile.get("name", "").strip()
            if name:
                return name
        except Exception:
            pass
    return ""
