"""Tests for the thin, library-based scripts/deploy.py (fake SSH, no network).

The deploy pipeline now lives entirely in ``ha_deploy_kit.cli.run``; this script
only supplies dwellant's identity (domain + served-card mapping) and delegates.
So these tests assert that configuration and that ``--check`` drives ``run``
without writing anything. The full behaviour matrix (frontend-only never
notifies, etc.) is owned and tested by the library.

Also pins the CLI contract: the legacy ``--restart`` / ``--no-restart`` /
``--card-only`` flags and the positional TARGET are gone.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_deploy_module():
    path = ROOT / "scripts" / "deploy.py"
    spec = importlib.util.spec_from_file_location("dwellant_deploy_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy_mod = _load_deploy_module()


class FakeSSH:
    """Record commands; answer the inventory probe with scripted output."""

    def __init__(self, inventory_output: bytes):
        self.inventory_output = inventory_output
        self.commands: list[tuple[str, bytes | None]] = []

    def __call__(self, command: str, stdin: bytes | None = None) -> bytes:
        self.commands.append((command, stdin))
        if "sha256sum" in command or "NO_REMOTE_TREE" in command:
            return self.inventory_output
        return b""

    def issued(self, prefix: str) -> list[str]:
        return [c for c, _ in self.commands if c.startswith(prefix)]


def test_build_settings_declares_dwellant_identity():
    settings = deploy_mod.build_settings(ROOT)
    assert settings.domain == "dwellant_packages"
    assert settings.source_root == ROOT
    assert settings.remote_config == "/config"
    assert settings.frontend is not None
    assert settings.frontend.source_rel == "lovelace/dwellant-packages-card.js"
    assert settings.frontend.published_remote == "www/dwellant/dwellant-packages-card.js"


def test_check_drives_run_without_writes():
    from ha_deploy_kit.cli import run

    settings = deploy_mod.build_settings(ROOT)
    fake = FakeSSH(b"NO_REMOTE_TREE\nMISSING WWW:card\n")

    rc = run(settings, ["--check"], ssh=fake, environ={})

    assert rc == 0
    assert fake.issued("mkdir") == []
    assert fake.issued("tar") == []


@pytest.mark.parametrize("argv", [["--restart"], ["--no-restart"], ["--card-only"]])
def test_legacy_restart_flags_removed(argv):
    from ha_deploy_kit.cli import run

    settings = deploy_mod.build_settings(ROOT)
    with pytest.raises(SystemExit):
        run(settings, argv, ssh=FakeSSH(b""), environ={})


def test_positional_target_removed():
    from ha_deploy_kit.cli import run

    settings = deploy_mod.build_settings(ROOT)
    with pytest.raises(SystemExit):
        run(settings, ["user@host"], ssh=FakeSSH(b""), environ={})
