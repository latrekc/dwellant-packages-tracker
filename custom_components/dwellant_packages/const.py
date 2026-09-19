"""Constants for the Dwellant Packages integration."""

from datetime import timedelta

DOMAIN = "dwellant_packages"
PLATFORMS = ["sensor"]


LOGIN_BASE_URL = "https://secure.dwellant.com"
LOGIN_URL = f"{LOGIN_BASE_URL}/Account/SignInOrRegister?ReturnUrl="


def table_url_for(base_url: str, org_id: int | str) -> str:
    """Available-packages table URL for a portal base address + Org ID."""
    return f"{base_url.rstrip('/')}/Org/{org_id}/DeliveredPackage/AvailablePackageTableRows"


# Key names only — values live in the config entry (HA .storage, i.e. the
# HA database), never in YAML and never hardcoded. A single config entry
# holds CONF_USERS: a list of {email, password} dicts; each user becomes
# its own device + sensor entities. Portal address and org ID are discovered
# automatically at login (central portal + org parsed from authorized page).
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_USERS = "users"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_RETENTION_DAYS = "retention_days"
CONF_NOTIFY_ARRIVAL = "notify_arrival"
CONF_NOTIFY_COLLECTION = "notify_collection"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
DEFAULT_RETENTION_DAYS = 30
MIN_SCAN_MINUTES = 5
MAX_SCAN_MINUTES = 60

EVENT_NEW_PACKAGE = "dwellant_new_package"
EVENT_PACKAGE_COLLECTED = "dwellant_package_collected"
EVENT_PACKAGES_UPDATE = "dwellant_packages_update"

# Collection digests list packages collected within this window so a bulk
# pickup produces one summary notification instead of one per poll.
COLLECTED_RECENT_HOURS = 24

STATUS_AVAILABLE = "available"
STATUS_COLLECTED = "collected"

ATTR_PACKAGES = "packages"
ATTR_COLLECTED = "collected"
ATTR_UNIT = "unit"
ATTR_EMAIL = "email"
ATTR_ORG_ID = "org_id"
ATTR_BASE_URL = "portal"
ATTR_LAST_UPDATED = "last_updated"
ATTR_RETENTION_DAYS = "retention_days"

STORAGE_VERSION = 1

# POST body for the available-packages table (see README for source).
TABLE_FORM = {
    "firstResult": "0",
    "maxResults": "21",
    "sortProperty": "",
    "isAscendingSort": "true",
}
TABLE_PAGE_SIZE = 21

# Known package-type labels as shown in the portal table.
PACKAGE_TYPES = (
    "A letter",
    "Package (Small)",
    "Package (Medium)",
    "Package (Large)",
    "Oversized/Heavy",
)
