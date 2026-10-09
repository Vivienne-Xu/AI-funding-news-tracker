"""`app alert`: a short email to the owner when a scheduled run fails."""

from __future__ import annotations

import html
from pathlib import Path

from app.config import Settings
from app.send import send_email

LOG_LINES = 25
MAX_LINE_CHARS = 300


def redact(text: str, settings: Settings) -> str:
    """Removes secret values, in case an error message ever contains one."""
    for secret in (settings.gemini_api_key, settings.resend_api_key):
        if len(secret) >= 8:
            text = text.replace(secret, "[hidden]")
    return text


def log_tail(path: Path | None, settings: Settings) -> str:
    """The last lines of the run's log, with secrets removed."""
    if path is None or not path.exists():
        return "(no log file was found)"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-LOG_LINES:]
    return redact("\n".join(line[:MAX_LINE_CHARS] for line in lines), settings) or "(the log is empty)"


def send_failure_alert(settings: Settings, job: str, link: str, log_file: Path | None) -> str:
    """Emails the failure. Returns the subject line."""
    subject = f"AI Funding: the {job} run failed"
    tail = log_tail(log_file, settings)
    link_line = f"Full log: {link}" if link else "No log link available."
    reassurance = "Nothing was half-saved or sent twice; the next run tries again from the same news."
    text = f"The scheduled {job} run did not finish. {reassurance}\n\n{link_line}\n\nLast lines of the log:\n{tail}\n"
    link_html = f'<a href="{html.escape(link)}">Open the full log</a>' if link else "No log link available."
    page = (
        f"<p>The scheduled <b>{html.escape(job)}</b> run did not finish. {reassurance}</p>"
        f"<p>{link_html}</p><p>Last lines of the log:</p><pre>{html.escape(tail)}</pre>"
    )
    send_email(settings, subject, page, text)
    return subject
