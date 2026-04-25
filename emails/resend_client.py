"""
Resend email client.

Sends post-activity analysis emails (and any other notifications) using the
Resend API. Wraps content in a clean HTML shell.

Resend SDK reference: https://resend.com/docs/send-with-python
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import resend

from config import settings
from db.logs import log_event

logger = logging.getLogger(__name__)


HTML_SHELL = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#f5f6f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2328;">
  <div style="max-width:640px;margin:32px auto;background:#ffffff;border:1px solid #e1e4e8;border-radius:10px;overflow:hidden;">
    <div style="padding:20px 28px;border-bottom:1px solid #e1e4e8;background:#0d1117;color:#f0f6fc;">
      <div style="font-size:12px;letter-spacing:1px;text-transform:uppercase;opacity:0.7;">AI Coach</div>
      <div style="font-size:18px;font-weight:600;margin-top:4px;">{subject}</div>
    </div>
    <div style="padding:24px 28px;line-height:1.55;font-size:15px;">
      {body_html}
    </div>
    <div style="padding:14px 28px;border-top:1px solid #e1e4e8;font-size:12px;color:#6e7681;background:#f6f8fa;">
      Sent by your personal coaching bot · {athlete_tz}
    </div>
  </div>
</body>
</html>
"""


def _format_html(subject: str, body_html: str) -> str:
    return HTML_SHELL.format(
        subject=subject,
        body_html=body_html,
        athlete_tz=settings.ATHLETE_TIMEZONE,
    )


async def send_email(
    subject: str,
    body_html: str,
    *,
    to: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Send an HTML email via Resend.

    Returns the Resend API response dict.
    Logs success / failure to the event log. Never raises — returns
    {"ok": False, "error": "..."} on failure so the caller can decide whether
    to retry or surface the error elsewhere.
    """
    if not settings.RESEND_API_KEY:
        msg = "RESEND_API_KEY not configured — skipping email send"
        logger.warning(msg)
        await log_event("email_skipped", msg, severity="warning", metadata=metadata)
        return {"ok": False, "error": "RESEND_API_KEY not set"}

    recipient = to or settings.RESEND_TO_EMAIL
    if not recipient:
        msg = "RESEND_TO_EMAIL not configured — skipping email send"
        logger.warning(msg)
        await log_event("email_skipped", msg, severity="warning", metadata=metadata)
        return {"ok": False, "error": "RESEND_TO_EMAIL not set"}

    resend.api_key = settings.RESEND_API_KEY

    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": [recipient],
        "subject": subject,
        "html": _format_html(subject, body_html),
    }

    # The resend SDK is sync — run it in a thread so we don't block the loop.
    def _send() -> Any:
        return resend.Emails.send(payload)

    try:
        result = await asyncio.to_thread(_send)
        await log_event(
            "email_sent",
            f"Email sent to {recipient}: {subject}",
            severity="info",
            metadata={"subject": subject, "to": recipient, **(metadata or {})},
        )
        return {"ok": True, "result": result}
    except Exception as exc:
        await log_event(
            "email_failed",
            f"Resend send failed: {exc}",
            severity="error",
            metadata={"subject": subject, "to": recipient, **(metadata or {})},
        )
        return {"ok": False, "error": str(exc)}
