"""IMAP enrichment: find Dwellant notification emails by collection code.

Server-side SEARCH only — no bulk fetch into HA. For each unknown
collection code we run a single ``UID SEARCH TEXT "<code>"`` and fetch at
most one matching message body, from which ``extract_gmail_description``
pulls the last paragraph. Anything missing/empty/failing -> None
(fail-open: the package stays as the portal reported it).
"""

from __future__ import annotations

import asyncio
import email as email_lib
import email.policy
import imaplib
import logging
from email.message import EmailMessage

from .const import CONF_IMAP_HOST, CONF_IMAP_PASSWORD, CONF_IMAP_USERNAME
from .parser import extract_gmail_description

_LOGGER = logging.getLogger(__name__)

DEFAULT_IMAP_HOST = "imap.gmail.com"
IMAP_SEARCH_LIMIT = 5


def _decode_body(msg: EmailMessage) -> str:
    """Prefer text/html part, fall back to text/plain. Never raises."""
    try:
        if msg.is_multipart():
            html_part: str | None = None
            text_part: str | None = None
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = part.get_content_disposition() or ""
                if disp == "attachment":
                    continue
                try:
                    payload = part.get_content()
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(payload, str):
                    continue
                if ctype == "text/html" and html_part is None:
                    html_part = payload
                elif ctype == "text/plain" and text_part is None:
                    text_part = payload
            return html_part or text_part or ""
        payload = msg.get_content()
        return payload if isinstance(payload, str) else ""
    except Exception:  # noqa: BLE001
        return ""


def _imap_blocking_search(
    host: str,
    username: str,
    password: str,
    codes: list[str],
) -> dict[str, dict]:
    """Blocking IMAP search+fetch. Runs in an executor thread.

    Returns {code: {"email_found": bool, "description": str | None}}.
    email_found=True means an email containing the code was located
    (description may still be None when the note is empty).
    """
    results: dict[str, dict] = {
        code: {"email_found": False, "description": None} for code in codes
    }
    try:
        imap = imaplib.IMAP4_SSL(host, timeout=30)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("IMAP connect to %s failed: %s", host, err)
        return results
    try:
        try:
            imap.login(username, password)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("IMAP login failed for %s: %s", username, err)
            return results
        try:
            imap.select("INBOX", readonly=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("IMAP select INBOX failed: %s", err)
            return results
        for code in codes:
            try:
                # Server-side search: only matching UIDs come back.
                status, data = imap.uid("SEARCH", "TEXT", f'"{code}"')
                if status != "OK" or not data or not data[0]:
                    continue
                uids = data[0].split()[-IMAP_SEARCH_LIMIT:]
                if not uids:
                    continue
                # Newest first; take the first whose body contains the code.
                for uid in reversed(uids):
                    fstatus, fdata = imap.uid("FETCH", uid, "(BODY[])")
                    if fstatus != "OK" or not fdata:
                        continue
                    raw = fdata[0][1] if isinstance(fdata[0], tuple) else None
                    if not raw:
                        continue
                    msg = email_lib.message_from_bytes(
                        raw, policy=email.policy.default
                    )
                    body = _decode_body(msg)
                    if code not in body:
                        continue
                    desc = extract_gmail_description(body)
                    results[code] = {
                        "email_found": True,
                        "description": desc,  # None when empty -> flag still True
                    }
                    break
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("IMAP search for code %s failed: %s", code, err)
                continue
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return results


class ImapEnrichmentClient:
    """Per-Dwellant-user IMAP enrichment (fail-open, server-side SEARCH)."""

    def __init__(self, email: str, imap_config: dict) -> None:
        self.email = email
        self.host = str(imap_config.get(CONF_IMAP_HOST) or DEFAULT_IMAP_HOST)
        self.username = str(imap_config.get(CONF_IMAP_USERNAME) or email)
        self.password = str(imap_config.get(CONF_IMAP_PASSWORD) or "")

    @property
    def enabled(self) -> bool:
        """Enrichment runs only when an app password is configured."""
        return bool(self.password)

    def imap_settings(self) -> dict:
        """Non-secret settings for diagnostics (never the password)."""
        return {"host": self.host, "username": self.username}

    async def async_fetch_descriptions(
        self, codes: list[str]
    ) -> dict[str, dict]:
        """SEARCH inbox for each code.

        Returns {code: {"email_found": bool, "description": str | None}}.
        Never raises: on any failure every code maps to email_found=False.
        """
        if not self.enabled or not codes:
            return {
                code: {"email_found": False, "description": None} for code in codes
            }
        try:
            return await asyncio.to_thread(
                _imap_blocking_search,
                self.host,
                self.username,
                self.password,
                list(codes),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("IMAP enrichment failed for %s: %s", self.email, err)
            return {
                code: {"email_found": False, "description": None} for code in codes
            }
