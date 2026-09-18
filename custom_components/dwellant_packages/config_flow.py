"""Config flow: single entry holding multiple Dwellant users (no YAML)."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CannotConnect, DwellantClient, InvalidAuth
from .const import (
    CONF_BASE_URL,
    CONF_EMAIL,
    CONF_NOTIFY_ARRIVAL,
    CONF_NOTIFY_COLLECTION,
    CONF_ORG_ID,
    CONF_PASSWORD,
    CONF_RETENTION_DAYS,
    CONF_SCAN_INTERVAL,
    CONF_USERS,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    MAX_SCAN_MINUTES,
    MIN_SCAN_MINUTES,
    normalize_base_url,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="email")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
        vol.Required(CONF_ORG_ID): NumberSelector(
            NumberSelectorConfig(
                min=1, max=999999, step=1, mode=NumberSelectorMode.BOX
            )
        ),
        vol.Required(CONF_BASE_URL): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.URL, autocomplete="url"
            )
        ),
    }
)


async def _validate_login(
    hass: HomeAssistant,
    email: str,
    password: str,
    org_id: int | str,
    base_url: str,
) -> None:
    """Raise InvalidAuth / CannotConnect when login fails."""
    client = DwellantClient(
        email=email, password=password, org_id=org_id, base_url=base_url
    )
    try:
        await client.async_login()
    finally:
        await client.async_close()


class DwellantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle initial setup (single entry, multi-user)."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        # Single entry: further users are added via Options.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=USER_SCHEMA)
        email = str(user_input[CONF_EMAIL]).strip()
        org_id = int(user_input[CONF_ORG_ID])
        base_url = normalize_base_url(str(user_input[CONF_BASE_URL]))
        if not base_url:
            return self.async_show_form(
                step_id="user",
                data_schema=USER_SCHEMA,
                errors={CONF_BASE_URL: "invalid_base_url"},
            )
        try:
            await _validate_login(
                self.hass, email, user_input[CONF_PASSWORD], org_id, base_url
            )
        except InvalidAuth:
            return self.async_show_form(
                step_id="user", data_schema=USER_SCHEMA, errors={"base": "invalid_auth"}
            )
        except CannotConnect:
            return self.async_show_form(
                step_id="user",
                data_schema=USER_SCHEMA,
                errors={"base": "cannot_connect"},
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected login validation error")
            return self.async_show_form(
                step_id="user", data_schema=USER_SCHEMA, errors={"base": "unknown"}
            )
        await self.async_set_unique_id(DOMAIN)
        return self.async_create_entry(
            title="Dwellant Packages",
            data={
                CONF_USERS: [
                    {
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ORG_ID: int(org_id),
                        CONF_BASE_URL: base_url,
                    }
                ],
                CONF_SCAN_INTERVAL: 5,
                CONF_RETENTION_DAYS: DEFAULT_RETENTION_DAYS,
                CONF_NOTIFY_ARRIVAL: True,
                CONF_NOTIFY_COLLECTION: True,
            },
        )

    async def async_step_reauth(self, user_input=None):
        """Triggered by ConfigEntryAuthFailed; updates the affected user."""
        self.context["reauth"] = True
        return await self.async_step_reauth_confirm(user_input)

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id", "")
        )
        if entry is None and self._async_current_entries():
            entry = self._async_current_entries()[0]
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm", data_schema=USER_SCHEMA
            )
        email = str(user_input[CONF_EMAIL]).strip()
        org_id = int(user_input[CONF_ORG_ID])
        base_url = normalize_base_url(str(user_input.get(CONF_BASE_URL, "")))
        if not base_url:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=USER_SCHEMA,
                errors={CONF_BASE_URL: "invalid_base_url"},
            )
        try:
            await _validate_login(
                self.hass, email, user_input[CONF_PASSWORD], org_id, base_url
            )
        except InvalidAuth:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=USER_SCHEMA,
                errors={"base": "invalid_auth"},
            )
        except CannotConnect:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=USER_SCHEMA,
                errors={"base": "cannot_connect"},
            )
        except Exception:  # noqa: BLE001
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=USER_SCHEMA,
                errors={"base": "unknown"},
            )
        if entry is not None:
            users = list(entry.data.get(CONF_USERS, []))
            updated = False
            for user in users:
                if str(user.get(CONF_EMAIL, "")).strip().lower() == email.lower():
                    user[CONF_PASSWORD] = user_input[CONF_PASSWORD]
                    user[CONF_ORG_ID] = org_id
                    user[CONF_BASE_URL] = base_url
                    updated = True
            if not updated:
                users.append(
                    {
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ORG_ID: org_id,
                        CONF_BASE_URL: base_url,
                    }
                )
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_USERS: users}
            )
            await self.hass.config_entries.async_reload(entry.entry_id)
        return self.async_abort(reason="reauth_successful")

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        """Return the options flow (user + settings management)."""
        return DwellantOptionsFlow(entry)


class DwellantOptionsFlow(config_entries.OptionsFlow):
    """Manage users and settings on the single entry."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._edit_email: str | None = None

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_user", "remove_user", "edit_user", "settings"],
        )

    async def async_step_add_user(self, user_input=None):
        if user_input is None:
            return self.async_show_form(step_id="add_user", data_schema=USER_SCHEMA)
        email = str(user_input[CONF_EMAIL]).strip()
        org_id = int(user_input[CONF_ORG_ID])
        base_url = normalize_base_url(str(user_input.get(CONF_BASE_URL, "")))
        if not base_url:
            return self.async_show_form(
                step_id="add_user",
                data_schema=USER_SCHEMA,
                errors={CONF_BASE_URL: "invalid_base_url"},
            )
        existing = {
            str(u.get(CONF_EMAIL, "")).strip().lower()
            for u in self._entry.data.get(CONF_USERS, [])
        }
        if email.lower() in existing:
            return self.async_show_form(
                step_id="add_user",
                data_schema=USER_SCHEMA,
                errors={CONF_EMAIL: "already_configured"},
            )
        try:
            await _validate_login(
                self.hass, email, user_input[CONF_PASSWORD], org_id, base_url
            )
        except InvalidAuth:
            return self.async_show_form(
                step_id="add_user",
                data_schema=USER_SCHEMA,
                errors={"base": "invalid_auth"},
            )
        except CannotConnect:
            return self.async_show_form(
                step_id="add_user",
                data_schema=USER_SCHEMA,
                errors={"base": "cannot_connect"},
            )
        except Exception:  # noqa: BLE001
            return self.async_show_form(
                step_id="add_user", data_schema=USER_SCHEMA, errors={"base": "unknown"}
            )
        users = list(self._entry.data.get(CONF_USERS, []))
        users.append(
            {
                CONF_EMAIL: email,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_ORG_ID: org_id,
                CONF_BASE_URL: base_url,
            }
        )
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, CONF_USERS: users}
        )
        return self.async_create_entry(title="", data={})

    async def async_step_remove_user(self, user_input=None):
        users = self._entry.data.get(CONF_USERS, [])
        if not users:
            return self.async_abort(reason="no_users")
        emails = [str(u.get(CONF_EMAIL, "")) for u in users]
        schema = vol.Schema({vol.Required(CONF_EMAIL): vol.In(emails)})
        if user_input is None:
            return self.async_show_form(step_id="remove_user", data_schema=schema)
        remaining = [
            u for u in users if str(u.get(CONF_EMAIL, "")) != user_input[CONF_EMAIL]
        ]
        if not remaining:
            return self.async_show_form(
                step_id="remove_user",
                data_schema=schema,
                errors={CONF_EMAIL: "keep_one_user"},
            )
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, CONF_USERS: remaining}
        )
        return self.async_create_entry(title="", data={})

    async def async_step_edit_user(self, user_input=None):
        """Change a user's portal address / Org ID (password re-checked live)."""
        users = self._entry.data.get(CONF_USERS, [])
        if not users:
            return self.async_abort(reason="no_users")
        if self._edit_email is None:
            emails = [str(u.get(CONF_EMAIL, "")) for u in users]
            schema = vol.Schema({vol.Required(CONF_EMAIL): vol.In(emails)})
            if user_input is None:
                return self.async_show_form(step_id="edit_user", data_schema=schema)
            self._edit_email = str(user_input[CONF_EMAIL])
        current = next(
            (u for u in users if str(u.get(CONF_EMAIL, "")) == self._edit_email),
            {},
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=str(current.get(CONF_BASE_URL, "")),
                ): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.URL, autocomplete="url"
                    )
                ),
                vol.Required(
                    CONF_ORG_ID,
                    default=int(current[CONF_ORG_ID]) if CONF_ORG_ID in current else None,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=999999, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    )
                ),
            }
        )
        if user_input is None or CONF_ORG_ID not in user_input:
            return self.async_show_form(step_id="edit_user", data_schema=schema)
        org_id = int(user_input[CONF_ORG_ID])
        base_url = normalize_base_url(str(user_input.get(CONF_BASE_URL, "")))
        if not base_url:
            return self.async_show_form(
                step_id="edit_user",
                data_schema=schema,
                errors={CONF_BASE_URL: "invalid_base_url"},
            )
        try:
            await _validate_login(
                self.hass,
                self._edit_email,
                user_input[CONF_PASSWORD],
                org_id,
                base_url,
            )
        except InvalidAuth:
            return self.async_show_form(
                step_id="edit_user",
                data_schema=schema,
                errors={"base": "invalid_auth"},
            )
        except CannotConnect:
            return self.async_show_form(
                step_id="edit_user",
                data_schema=schema,
                errors={"base": "cannot_connect"},
            )
        except Exception:  # noqa: BLE001
            return self.async_show_form(
                step_id="edit_user", data_schema=schema, errors={"base": "unknown"}
            )
        updated_users = []
        for user in users:
            if str(user.get(CONF_EMAIL, "")) == self._edit_email:
                updated_users.append(
                    {
                        CONF_EMAIL: user.get(CONF_EMAIL),
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ORG_ID: org_id,
                        CONF_BASE_URL: base_url,
                    }
                )
            else:
                updated_users.append(user)
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, CONF_USERS: updated_users}
        )
        return self.async_create_entry(title="", data={})

    async def async_step_settings(self, user_input=None):
        data = self._entry.data
        opts = self._entry.options
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(
                            CONF_SCAN_INTERVAL, data.get(CONF_SCAN_INTERVAL, 5)
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_MINUTES,
                            max=MAX_SCAN_MINUTES,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="min",
                        )
                    ),
                    vol.Required(
                        CONF_RETENTION_DAYS,
                        default=opts.get(
                            CONF_RETENTION_DAYS,
                            data.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS),
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=365, step=1, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_NOTIFY_ARRIVAL,
                        default=opts.get(
                            CONF_NOTIFY_ARRIVAL, data.get(CONF_NOTIFY_ARRIVAL, True)
                        ),
                    ): bool,
                    vol.Required(
                        CONF_NOTIFY_COLLECTION,
                        default=opts.get(
                            CONF_NOTIFY_COLLECTION,
                            data.get(CONF_NOTIFY_COLLECTION, True),
                        ),
                    ): bool,
                }
            )
            return self.async_show_form(step_id="settings", data_schema=schema)
        return self.async_create_entry(title="", data=user_input)
