# dwellant-packages-tracker

Home Assistant custom integration that tracks parcels in the Dwellant resident
portal (`secure.dwellant.com`). One config entry holds **multiple users** —
each login needs just email + password and becomes its own device + sensor.
No YAML, no hardcoded credentials: everything lives in the HA database
(`.storage`). The portal address and organisation ID are discovered
automatically at login (central sign-in + org parsed from the authorized page).

## How it works

1. **Setup (UI only):** Settings → Devices & Services → Add Integration →
   Dwellant Packages → enter email + password (validated live against the
   central portal). Add more users later via **Options → Add user**.
2. **Login:** GET the central sign-in page, submit the server-issued `save`
   nonce with credentials; success = `DwellantAuthentication` cookie. Org ID
   is parsed from the authorized page (`PATH_PREFIX` / `/Org/<id>/` links).
3. **Polling:** every 5 min (configurable 5–60) each user's session POSTs
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
`unit`, `email`, `org_id` + `portal` (auto-discovered at login),
`last_updated`.

## Login form contract (verified against the live portal)

- Sign-in page: `GET https://secure.dwellant.com/Account/SignInOrRegister?ReturnUrl=`
- Required POST fields: `save` (server-issued nonce from the form),
  `displayNameOrEmailAddress`, `password`, `rememberMe=True`,
  `saveChanges=Sign in`.
- Success signal: `DwellantAuthentication` cookie (+ redirect to an
  authorized page). The old per-site `woodberry-secure` login without the
  `save` nonce silently returns the login page again — that was the auth bug.

## Tests

`python3 -m pytest tests/ -q` — pure parser + state diff tests, no HA needed.
