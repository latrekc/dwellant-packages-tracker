"""Guard the integration manifest requirements (no HA needed).

Home Assistant installs every custom integration's ``requirements`` into a
single shared Python environment, so a library that another integration also
uses must not be hard-pinned with ``==``: when two integrations disagree on the
exact version, whichever installs last wins and breaks the other. Each
requirement therefore declares a lower bound (``>=x.y.z``) at this integration's
true minimum, accepting any newer compatible release. beautifulsoup4 must stay
declared. A git/VCS URL pin is still rejected: HA cannot resolve it in its own
environment.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "dwellant_packages" / "manifest.json"

# A PyPI floor pin: ``ha-deploy-kit>=x.y.z`` with an optional upper bound
# (e.g. ``>=0.1.4,<1``). Exact ``==`` pins are rejected to stay coexistence-safe.
_VERSION_PIN_RE = re.compile(r"^ha-deploy-kit>=\d+\.\d+\.\d+(,<\S+)?$")


def _requirements() -> list[str]:
    data = json.loads(MANIFEST.read_text())
    reqs = data["requirements"]
    assert isinstance(reqs, list)
    return reqs


def _req_name(req: str) -> str:
    return re.split(r"[<>=!~]", req, maxsplit=1)[0].strip()


def test_beautifulsoup_still_declared():
    assert any(_req_name(r) == "beautifulsoup4" for r in _requirements())


def test_no_shared_requirement_uses_exact_pin():
    """No requirement may hard-pin with ``==`` in the shared HA runtime env."""
    exact = [r for r in _requirements() if "==" in r]
    assert not exact, f"exact == pins break shared-env coexistence: {exact}"


def test_ha_deploy_kit_pinned_to_version_floor():
    pins = [r for r in _requirements() if _req_name(r) == "ha-deploy-kit"]
    assert len(pins) == 1, pins
    assert _VERSION_PIN_RE.match(pins[0]), (
        f"expected a PyPI version floor pin (>=x.y.z), got: {pins[0]!r}"
    )


def test_no_vcs_or_url_pin():
    for req in _requirements():
        if _req_name(req) == "ha-deploy-kit":
            assert "@" not in _req_name(req), f"unexpected URL pin: {req!r}"
            assert "git+" not in req, f"git pin not installable by HA: {req!r}"
