# dwellant-packages-tracker

Home Assistant custom integration that tracks parcels in a Dwellant resident
portal. One config entry holds **multiple users** — each login has its own
portal address, email, password and **organisation ID** (the `/Org/<id>/`
number from the portal URL, all required) and becomes its own device + sensor.
No YAML, no hardcoded credentials or addresses: everything lives in the HA
database (`.storage`).

## How it works

1. **Setup (UI only):** Settings → Devices & Services → Add Integration →
   Dwellant Packages → enter first user's portal address (e.g.
   `https://your-site.dwellant.com`), email + password + organisation ID
   (the `/Org/<id>/` number from your portal URL, validated live).
   Add more users later via the integration's **Options → Add user**; change
   a user's portal address or org via **Options → Edit user**.
2. **Polling:** every 5 min (configurable 5–60) each user's session POSTs
   `/Org/<org_id>/DeliveredPackage/AvailablePackageTableRows`
   (`firstResult/maxResults/sortProperty/isAscendingSort`), parses the `<tr>`
   rows (Unit, Type, Collection Code, Delivery Time, Concierge).
3. **Tracking:** collection code is the stable key. New code = arrived; code
   gone after a successful fetch = collected. Collected items are **kept**
   (collapsed in the card), pruned after 30 days (configurable, 0 = forever).
4. **Notifications (aggregated):** one persistent notification per user, rewritten
   each poll — `Waiting for pickup (N)` + `Recently collected (M, last 24h)`
   summaries, never one ping per package. Fires per-package
   `dwellant_new_package` / `dwellant_package_collected` events (codes stay as
   internal tracking keys) plus one `dwellant_packages_update` count event per
   poll. Both notification halves toggleable. See `automations.example.yaml`
   and `blueprints/`.
5. **Card:** `custom_components/dwellant_packages/lovelace/dwellant-packages-card.js`
   — per-user table (type + delivered with relative time like "5 hours ago",
   codes/concierge never shown); collected history shows type + dates only.

## Install (manual, no HACS)

1. Copy `custom_components/dwellant_packages` into your HA `config/`.
2. Restart HA. The card auto-registers; fallback: copy the `.js` to
   `config/www/` and add a Lovelace resource `/local/dwellant-packages-card.js`.
3. Add the integration, then add the card per user:
   `type: custom:dwellant-packages-card, entity: sensor.dwellant_<email>`.

## Sensors

Per user: state = available count; attributes `packages` (sorted, with
`code/unit/type/delivery_time/delivery_time_raw/concierge/icon` — `code` is
an internal tracking key, never rendered), `collected` (recent first),
`unit`, `email`, `org_id`, `portal`, `last_updated`.

## Verify before first real login

The login form field names (`__RequestVerificationToken`, `returnUrl`,
`displayNameOrEmailAddress`, `checkbox`/`saveChanges`) and the
session-expiry signal were derived from a devtools snapshot — confirm once
against the live portal (GET login page → token names; POST → success
redirect to `/YourInformation`).

## Tests

`python3 -m pytest tests/ -q` — pure parser + state diff tests, no HA needed.
