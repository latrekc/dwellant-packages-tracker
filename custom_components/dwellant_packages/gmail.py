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


def _all_mail_folder(list_data: list[bytes] | None) -> str | None:
    """Pick the archive folder from a LIST response via the \\All flag.

    Folder names are localized per account (English ``"[Gmail]/All Mail"``
    vs Russian ``"[Gmail]/&BBIEQQRP- &BD8EPgRHBEIEMA-"``), so the name is
    never hardcoded — the ``\\All`` flag is the locale-independent marker.
    Returns the quoted folder name ready for SELECT, or None.
    """
    for entry in list_data or []:
        try:
            text = entry.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        # e.g. (\All \HasNoChildren) "/" "[Gmail]/All Mail" — the flags
        # section (before ")") carries \All; the name may be localized.
        flags = text.split(")")[0] if ")" in text else text
        if "\\all" not in flags.lower():
            continue
        # Folder name is the last quoted string.
        name = text.rsplit('"', 2)
        if len(name) >= 3:
            return f'"{name[-2]}"'
    return None


def _imap_blocking_search(
    host: str,
    username: str,
    password: str,
    codes: list[str],
) -> dict[str, dict]:
    """Blocking IMAP search+fetch. Runs in an executor thread.

    Returns {"results": {code: {...}}, "auth_failed": bool}. auth_failed is
    True only when the server rejected the credentials (wrong/expired app
    password) — as opposed to a transient network error. email_found=True
    means an email containing the code was located (description may still
    be None when the note is empty).
    """
    results: dict[str, dict] = {
        code: {"email_found": False, "description": None} for code in codes
    }
    outcome: dict = {"results": results, "auth_failed": False}
    try:
        imap = imaplib.IMAP4_SSL(host, timeout=30)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("IMAP connect to %s failed: %s", host, err)
        return outcome
    try:
        try:
            imap.login(username, password)
        except imaplib.IMAP4.error as err:
            # Credentials rejected (e.g. regular password instead of an app
            # password, or a revoked one) — visible, actionable, logged once
            # per poll at warning, not hidden at debug.
            _LOGGER.warning(
                "IMAP login failed for %s: %s. Use a Gmail App Password "
                "(myaccount.google.com/apppasswords), not your normal password.",
                username,
                err,
            )
            outcome["auth_failed"] = True
            return outcome
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("IMAP login failed for %s: %s", username, err)
            return outcome
        # Concierge mail is auto-archived out of INBOX, so search the archive
        # folder FIRST (it covers everything, INBOX included). The folder name
        # is localized per account (English "[Gmail]/All Mail" vs Russian
        # "[Gmail]/&BBIEQQRP- &BD8EPgRHBEIEMA-"), so pick it via the \All
        # flag from LIST, never by hardcoded name. INBOX is the fallback for
        # non-Gmail servers. NOTE: imaplib returns ("NO", ...) rather than
        # raising on a bad folder, so CHECK the select status.
        list_data: list[bytes] | None = None
        try:
            _, list_data = imap.list()
        except Exception:  # noqa: BLE001
            pass
        all_mail = _all_mail_folder(list_data)
        mailbox = "INBOX"  # default; overwritten when archive SELECT succeeds
        selected = False
        if all_mail is not None:
            sel_status, _ = imap.select(all_mail, readonly=True)
            if sel_status == "OK":
                mailbox = "Archive"
                selected = True
        if not selected:
            try:
                sel_status, _ = imap.select("INBOX", readonly=True)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("IMAP select failed for %s", username)
                return outcome
            if sel_status != "OK":
                return outcome
            mailbox = "INBOX"
        for code in codes:
            try:
                # Server-side search: only matching UIDs come back.
                status, data = imap.uid("SEARCH", "TEXT", f'"{code}"')
                uids = []
                if status == "OK" and data and data[0]:
                    uids = data[0].split()[-IMAP_SEARCH_LIMIT:]
                if not uids and mailbox != "INBOX":
                    # Archive empty (or non-Gmail): try INBOX explicitly,
                    # then reselect the archive folder we came from.
                    try:
                        imap.select("INBOX", readonly=True)
                        istatus, idata = imap.uid("SEARCH", "TEXT", f'"{code}"')
                        if istatus == "OK" and idata and idata[0]:
                            uids = idata[0].split()[-IMAP_SEARCH_LIMIT:]
                        if all_mail is not None:
                            imap.select(all_mail, readonly=True)
                    except Exception:  # noqa: BLE001
                        pass
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
                    _LOGGER.debug(
                        "IMAP match for code %s on %s: found=%s",
                        code,
                        mailbox,
                        desc is not None,
                    )
                    break
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("IMAP search for code %s failed: %s", code, err)
                continue
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return outcome


class ImapEnrichmentClient:
    """Per-Dwellant-user IMAP enrichment (fail-open, server-side SEARCH)."""

    def __init__(self, email: str, imap_config: dict) -> None:
        self.email = email
        self.host = str(imap_config.get(CONF_IMAP_HOST) or DEFAULT_IMAP_HOST)
        self.username = str(imap_config.get(CONF_IMAP_USERNAME) or email)
        # Belt-and-braces: stored passwords predate space-stripping, and
        # Google shows app passwords grouped with spaces.
        self.password = "".join(
            str(imap_config.get(CONF_IMAP_PASSWORD) or "").split()
        )
        # Set after each fetch: server rejected the credentials (wrong or
        # revoked app password). Coordinator surfaces it as a repair issue.
        self.auth_failed = False

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
        Sets self.auth_failed when the server rejected the credentials.
        Never raises: on any failure every code maps to email_found=False.
        """
        self.auth_failed = False
        if not self.enabled or not codes:
            return {
                code: {"email_found": False, "description": None} for code in codes
            }
        try:
            outcome = await asyncio.to_thread(
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
        if outcome.get("auth_failed"):
            self.auth_failed = True
        return outcome.get("results", {})
