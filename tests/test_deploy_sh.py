"""Test the self-bootstrapping scripts/deploy.sh wrapper.

``deploy.sh`` must run deploy.py against the local ``.venv`` (which holds
``ha-deploy-kit``) WITHOUT the caller activating anything, even when the ambient
``python3`` lacks the dependency. We prove this with a minimal PATH (system
python only, no library); ``--help`` must still succeed via the managed venv.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENV_PY = ROOT / ".venv" / "bin" / "python"

pytestmark = pytest.mark.skipif(
    not VENV_PY.exists(),
    reason="managed .venv not provisioned in this environment",
)


def test_wrapper_uses_managed_venv_without_activation():
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}
    result = subprocess.run(
        [str(ROOT / "scripts" / "deploy.sh"), "--help"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage: ha-deploy" in result.stdout
