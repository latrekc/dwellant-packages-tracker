"""Async client for the Dwellant resident portal.

One DwellantClient instance = one portal user (own cookies). The
coordinator creates one client per user in the config entry.

Login flow (central portal, org auto-discovered):
  1. GET https://secure.dwellant.com/Account/SignInOrRegister?ReturnUrl=
     -> harvest the server-issued ``save`` nonce (required on every POST).
  2. POST email + password + save nonce + rememberMe fields.
  3. Success = DwellantAuthentication cookie set AND/OR redirect to an
     authorized page. Org ID is parsed from that page
     (PATH_PREFIX / /Org/<id>/ links) — no user configuration needed.
"""

from __future__ import annotations

import logging

import aiohttp

from .const import LOGIN_BASE_URL, LOGIN_URL, TABLE_FORM, TABLE_PAGE_SIZE, table_url_for
from .parser import (
    extract_org_id,
    extract_save_nonce,
    looks_like_login_page,
    parse_available_table,
)

_LOGGER = logging.getLogger(__name__)

_AUTH_COOKIES = ("DwellantAuthentication", "DwellantAuthenticationNonce")


class InvalidAuth(Exception):
    """Credentials were rejected."""


class CannotConnect(Exception):
    """Portal could not be reached or returned an unexpected response."""


class SessionExpired(Exception):
    """Session cookie no longer valid (re-login + retry already attempted)."""


# Backwards-compat alias (was private).
_SessionExpired = SessionExpired


def _cookie_names(session: aiohttp.ClientSession, url: str) -> list[str]:
    """Return cookie names (never values) held for a URL. Best effort."""
    try:
        return sorted(session.cookie_jar.filter_cookies(url).keys())
    except Exception:  # noqa: BLE001
        return []


def _has_auth_cookie(session: aiohttp.ClientSession) -> bool:
    """True when the session holds the portal's auth cookie (any domain)."""
    try:
        for cookie in session.cookie_jar:
            if cookie.key in _AUTH_COOKIES and cookie.value:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _login_error_hint(html: str) -> str:
    """Extract a short server-side error hint (no credentials) from a login page."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html or "", "html.parser")
        for selector in (".validation-summary-errors", ".field-validation-error"):
            tag = soup.select_one(selector)
            if tag:
                text = " ".join(tag.get_text(" ", strip=True).split())
                if text:
                    return text[:200]
    except Exception:  # noqa: BLE001
        pass
    return ""


class DwellantClient:
    """Logged-in session for a single Dwellant user.

    Each user gets an ISOLATED cookie jar (own ClientSession) so multiple
    logins never mix cookies. Org ID and portal base are discovered at
    login; the table endpoint host follows the login host.
    """

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._session = session
        self._own_session = session is None
        self.email = email
        self._password = password
        self.org_id: int | None = None
        self.base_url: str = LOGIN_BASE_URL
        self._logged_in = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=False)
            )
            self._own_session = True
        return self._session

    async def async_close(self) -> None:
        """Close the owned session (no-op for shared sessions)."""
        if self._own_session and self._session is not None:
            try:
                await self._session.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._session = None
                self._logged_in = False

    def update_password(self, password: str) -> None:
        """Update stored password (after reauth) and force re-login."""
        self._password = password
        self._logged_in = False

    async def async_login(self) -> None:
        """Establish a session. Raises InvalidAuth / CannotConnect."""
        session = await self._get_session()
        _LOGGER.debug("Dwellant login start for %s url=%s", self.email, LOGIN_URL)
        try:
            async with session.get(LOGIN_URL, timeout=30) as resp:
                login_html = await resp.text()
                _LOGGER.debug(
                    "Dwellant login GET for %s status=%s len=%d cookies=%s",
                    self.email,
                    resp.status,
                    len(login_html),
                    _cookie_names(session, LOGIN_URL),
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"GET login page failed: {err}") from err

        nonce = extract_save_nonce(login_html)
        _LOGGER.debug(
            "Dwellant login save-nonce for %s present=%s",
            self.email,
            nonce is not None,
        )
        if not nonce:
            raise CannotConnect("Sign-in form changed: no save nonce found")

        form = {
            "save": nonce,
            "returnUrl": "",
            "displayNameOrEmailAddress": self.email,
            "password": self._password,
            "rememberMe": "True",
            "saveChanges": "Sign in",
        }

        try:
            async with session.post(
                LOGIN_URL,
                data=form,
                timeout=30,
                allow_redirects=True,
            ) as resp:
                body = await resp.text()
                final_url = str(resp.url)
                history = [str(r.url) for r in resp.history]
                authed = _has_auth_cookie(session)
                _LOGGER.debug(
                    "Dwellant login POST for %s status=%s final=%s "
                    "redirects=%s body_len=%d looks_login=%s authed=%s cookies=%s",
                    self.email,
                    resp.status,
                    final_url,
                    history,
                    len(body),
                    looks_like_login_page(body),
                    authed,
                    _cookie_names(session, final_url),
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"POST login failed: {err}") from err

        if not authed or looks_like_login_page(body):
            _LOGGER.debug(
                "Dwellant login REJECTED for %s final=%s error_hint=%s",
                self.email,
                final_url,
                _login_error_hint(body),
            )
            raise InvalidAuth("Portal rejected the credentials")

        # Table host follows the post-login host (may differ from secure.).
        try:
            from urllib.parse import urlsplit

            parts = urlsplit(final_url)
            if parts.scheme and parts.netloc:
                self.base_url = f"{parts.scheme}://{parts.netloc}"
        except Exception:  # noqa: BLE001
            pass
        org_id = extract_org_id(body) or extract_org_id(final_url)
        if org_id is None:
            # The POST often lands on an intermediate page without org
            # markers — fetch the authorized home page and parse that instead.
            org_id = await self._async_discover_org(session)
        if org_id is None:
            raise CannotConnect(
                "Login succeeded but the Org ID was not found on the "
                "authorized pages; the portal layout may have changed"
            )
        if self.org_id != org_id:
            _LOGGER.debug(
                "Dwellant org discovered for %s: %s", self.email, org_id
            )
        self.org_id = org_id
        self._logged_in = True
        _LOGGER.debug(
            "Dwellant login OK for %s org=%s base=%s",
            self.email,
            self.org_id,
            self.base_url,
        )

    async def _async_discover_org(self, session: aiohttp.ClientSession) -> int | None:
        """Fetch authorized pages and parse the Org ID from them.

        Probes, in order: the YourInformation page (post-login landing),
        the portal root, and the final login URL. Returns None when no
        marker is found anywhere.
        """
        candidates = [
            f"{self.base_url}/YourInformation",
            f"{self.base_url}/",
            LOGIN_URL,
        ]
        for url in candidates:
            try:
                async with session.get(url, timeout=30, allow_redirects=True) as resp:
                    body = await resp.text()
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug(
                    "Dwellant org discovery GET %s failed for %s: %s",
                    url,
                    self.email,
                    err,
                )
                continue
            org_id = extract_org_id(body) or extract_org_id(str(resp.url))
            _LOGGER.debug(
                "Dwellant org discovery for %s url=%s final=%s len=%d org=%s",
                self.email,
                url,
                resp.url,
                len(body),
                org_id,
            )
            if org_id is not None:
                return org_id
        return None

    async def async_ensure_login(self) -> None:
        """Log in if we have no live session yet."""
        if not self._logged_in:
            await self.async_login()

    async def async_fetch_available(self) -> dict[str, dict]:
        """Fetch + parse the available-packages table (all pages).

        Transparently re-logs in once when the session expired.
        """
        await self.async_ensure_login()
        try:
            return await self._async_fetch_all_pages()
        except _SessionExpired:
            _LOGGER.debug("Session expired for %s, re-logging in", self.email)
            self._logged_in = False
            await self.async_login()
            return await self._async_fetch_all_pages()

    async def _async_fetch_all_pages(self) -> dict[str, dict]:
        """Walk firstResult pages until a short/empty page."""
        if self.org_id is None:
            raise CannotConnect("Org ID unknown: login did not discover it")
        packages: dict[str, dict] = {}
        first = 0
        table_url = table_url_for(self.base_url, self.org_id)
        while True:
            form = dict(TABLE_FORM)
            form["firstResult"] = str(first)
            session = await self._get_session()
            try:
                async with session.post(
                    table_url,
                    data=form,
                    timeout=30,
                ) as resp:
                    if resp.status in (401, 403):
                        _LOGGER.debug(
                            "Dwellant table fetch for %s url=%s status=%s "
                            "-> session expired",
                            self.email,
                            table_url,
                            resp.status,
                        )
                        raise _SessionExpired(f"HTTP {resp.status}")
                    body = await resp.text()
                    looks_login = looks_like_login_page(body)
                    _LOGGER.debug(
                        "Dwellant table fetch for %s url=%s first=%s status=%s "
                        "body_len=%d looks_login=%s sent_cookies=%s",
                        self.email,
                        table_url,
                        first,
                        resp.status,
                        len(body),
                        looks_login,
                        _cookie_names(session, table_url),
                    )
                    if resp.status == 200 and looks_login:
                        raise _SessionExpired("login form returned")
                    if resp.status != 200:
                        raise CannotConnect(f"Table fetch HTTP {resp.status}")
            except (aiohttp.ClientError, TimeoutError) as err:
                raise CannotConnect(f"Table fetch failed: {err}") from err

            page = parse_available_table(body)
            packages.update(page)
            if len(page) < TABLE_PAGE_SIZE:
                break
            first += len(page)
        return packages
