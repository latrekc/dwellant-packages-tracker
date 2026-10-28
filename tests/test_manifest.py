"""Guard the integration manifest requirements (no HA needed).

The ha-deploy-kit runtime dependency must be pinned to an immutable full commit
SHA (never a branch), and beautifulsoup4 must stay declared.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "dwellant_packages" / "manifest.json"

_GIT_PIN_RE = re.compile(
    r"^ha-deploy-kit @ git\+\S+@([0-9a-f]{40})$"
)


def _requirements() -> list[str]:
    data = json.loads(MANIFEST.read_text())
    reqs = data["requirements"]
    assert isinstance(reqs, list)
    return reqs


def test_beautifulsoup_still_declared():
    assert any(r.startswith("beautifulsoup4") for r in _requirements())


def test_ha_deploy_kit_pinned_to_full_sha():
    pins = [r for r in _requirements() if r.startswith("ha-deploy-kit")]
    assert len(pins) == 1, pins
    match = _GIT_PIN_RE.match(pins[0])
    assert match, f"expected git pin to a 40-char SHA, got: {pins[0]!r}"


def test_no_branch_pin():
    for req in _requirements():
        if req.startswith("ha-deploy-kit"):
            # A branch pin ends in a non-40-hex ref (e.g. @main); reject it.
            ref = req.rsplit("@", 1)[-1]
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"not a full SHA: {ref!r}"
