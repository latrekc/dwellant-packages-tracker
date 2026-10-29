"""Guard the integration manifest requirements (no HA needed).

The ha-deploy-kit runtime dependency must be pinned to an exact PyPI version
(``==x.y.z``) so Home Assistant can install it at startup, and beautifulsoup4
must stay declared. A git/VCS URL pin is rejected: HA cannot resolve it in its
own environment.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "dwellant_packages" / "manifest.json"

_VERSION_PIN_RE = re.compile(r"^ha-deploy-kit==\d+\.\d+\.\d+$")


def _requirements() -> list[str]:
    data = json.loads(MANIFEST.read_text())
    reqs = data["requirements"]
    assert isinstance(reqs, list)
    return reqs


def test_beautifulsoup_still_declared():
    assert any(r.startswith("beautifulsoup4") for r in _requirements())


def test_ha_deploy_kit_pinned_to_exact_version():
    pins = [r for r in _requirements() if r.startswith("ha-deploy-kit")]
    assert len(pins) == 1, pins
    assert _VERSION_PIN_RE.match(pins[0]), (
        f"expected an exact PyPI version pin, got: {pins[0]!r}"
    )


def test_no_vcs_or_url_pin():
    for req in _requirements():
        if req.startswith("ha-deploy-kit"):
            assert "@" not in req.split("==", 1)[0], f"unexpected URL pin: {req!r}"
            assert "git+" not in req, f"git pin not installable by HA: {req!r}"
