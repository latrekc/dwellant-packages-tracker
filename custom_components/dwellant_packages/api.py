"""Async client for the Dwellant resident portal.

One DwellantClient instance = one portal user (own cookies). The
coordinator creates one client per user in the config entry.
"""

from __future__ import annotations

import logging

import aiohttp

from .const import TABLE_FORM, TABLE_PAGE_SIZE, login_url_for, table_url_for
from .parser import (
    extract_request_verification_token,
    looks_like_login_page,
    parse_available_table,
)

_LOGGER = logging.getLogger(__name__)


class InvalidAuth(Exception):
    """Credentials were rejected."""


class CannotConnect(Exception):
    """Portal could not be reached or returned an unexpected response."""


class DwellantClient:
    """Logged-in session for a single Dwellant user.

    Each user gets an ISOLATED cookie jar (own ClientSession) so multiple
    logins never mix cookies. Pass a session only for one-off calls
    (e.g. config-flow validation); long-lived clients should own theirs.
    """

    def __init__(
        self,
        email: str,
        password: str,
        org_id: int | str,
        base_url: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._session = session
        self._own_session = session is None
        self.email = email
        self._password = password
        self.org_id = org_id
        self.base_url = base_url
        self._logged_in = False

    def update_org_id(self, org_id: int | str) -> None:
        """Update org and force re-login (table URL changes)."""
        if str(org_id) != str(self.org_id):
            self.org_id = org_id
            self._logged_in = False

    def update_base_url(self, base_url: str) -> None:
        """Update portal address and force re-login (all URLs change)."""
        if base_url != self.base_url:
            self.base_url = base_url
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
        login_url = login_url_for(self.base_url)
        try:
            async with session.get(login_url, timeout=30) as resp:
                login_html = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"GET login page failed: {err}") from err

        token = extract_request_verification_token(login_html)
        form = {
            "returnUrl": "/YourInformation",
            "displayNameOrEmailAddress": self.email,
            "password": self._password,
            "checkbox": "true",
            "saveChanges": "Sign in",
        }
        if token:
            form["__RequestVerificationToken"] = token

        try:
            async with session.post(
                login_url,
                data=form,
                timeout=30,
                allow_redirects=True,
            ) as resp:
                body = await resp.text()
                final_url = str(resp.url)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"POST login failed: {err}") from err

        if looks_like_login_page(body) and "YourInformation" not in final_url:
            raise InvalidAuth("Portal rejected the credentials")
        self._logged_in = True
        _LOGGER.debug("Dwellant login OK for %s", self.email)

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
        packages: dict[str, dict] = {}
        first = 0
        while True:
            form = dict(TABLE_FORM)
            form["firstResult"] = str(first)
            session = await self._get_session()
            try:
                async with session.post(
                    table_url_for(self.base_url, self.org_id),
                    data=form,
                    timeout=30,
                ) as resp:
                    if resp.status in (401, 403):
                        raise _SessionExpired(f"HTTP {resp.status}")
                    body = await resp.text()
                    if resp.status == 200 and looks_like_login_page(body):
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


class _SessionExpired(Exception):
    """Internal: session cookie no longer valid, re-login and retry once."""
