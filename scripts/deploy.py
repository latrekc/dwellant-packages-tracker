#!/usr/bin/env python3
"""Deploy the dwellant_packages integration to Home Assistant over SSH.

A thin wrapper around ``ha-deploy-kit``: it declares dwellant's identity (the
component domain and the served Lovelace card mapping) and hands off to the
library's ``run`` pipeline, which compares the local tree against the HA host and
deploys only the changed files.

The real default DEPLOYS (not read-only); pass ``--check`` / ``--dry-run`` to
preview without writing anything. The engine never restarts Home Assistant: a
backend change calls the integration's ``raise_restart_issue`` service over the
HA API (the one-click "Restart required" Repairs issue); a card-only change is
published separately and never raises that notice.

Runtime config comes from a ``.env`` next to the repo root (see ``.env.example``,
git-ignored): ``TARGET`` for SSH, ``HA_BASE_URL`` + ``HA_TOKEN`` for the API
call, optional ``REMOTE_CONFIG``. Explicit environment variables override it.

This file imports only the HA-free core ``ha_deploy_kit`` package.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ha_deploy_kit import DeploySettings, FrontendAsset
from ha_deploy_kit.cli import run

DOMAIN = "dwellant_packages"
FRONTEND = FrontendAsset(
    source_rel="lovelace/dwellant-packages-card.js",
    published_remote="www/dwellant/dwellant-packages-card.js",
)


def build_settings(root: Path) -> DeploySettings:
    return DeploySettings(domain=DOMAIN, source_root=root, frontend=FRONTEND)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parent.parent
    return run(build_settings(root), argv)


if __name__ == "__main__":
    raise SystemExit(main())
