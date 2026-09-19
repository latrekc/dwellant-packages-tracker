"""Deploy dwellant_packages to Home Assistant over SSH, with change detection.

Compares local files against the HA instance and:
  - exits early when already in sync (nothing to deploy),
  - copies only changed files (tar stream, skips __pycache__/*.pyc),
  - NEVER restarts HA on its own (HACS model): when backend files changed
    (*.py, manifest.json, strings/translations) it drops a marker file on
    the HA host; the running integration sees it on its next poll and raises
    a "Restart required" Repairs issue itself, with a one-click restart
    button. Pass --restart to actually restart.
  Card-only changes never need a restart.
Stale remote files (present on HA, missing locally) are reported, never deleted.

Optional `.env` file next to this script (see `.env.example`, git-ignored):
TARGET for SSH, HA_BASE_URL + HA_TOKEN for the HA API call from this
machine. Explicit environment variables override `.env`.

Usage:
  ./scripts/deploy.py [--check] [--restart] [--no-restart] [--card-only] [TARGET]
  TARGET=user@host ./scripts/deploy.py   # reads TARGET from .env

Stdlib only. No deploys run on import.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

COMP = "dwellant_packages"
REMOTE_COMP = "config/custom_components/dwellant_packages"
REMOTE_WWW_DIR = "config/www/dwellant"
CARD_REL = "dwellant_packages/lovelace/dwellant-packages-card.js"
CARD_WWW = "config/www/dwellant/dwellant-packages-card.js"
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc",)

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_COMP = REPO_ROOT / "custom_components" / COMP
CARD_LOCAL = REPO_ROOT / "custom_components" / CARD_REL
ENV_FILE = REPO_ROOT / ".env"


def load_dotenv(path: Path = ENV_FILE) -> dict[str, str]:
    """Load KEY=VALUE pairs from .env (stdlib only, no dependency).

    - Lines starting with # and blank lines are ignored.
    - Optional `export ` prefix and single/double quotes are stripped.
    - Only fills variables NOT already present in the environment.
    - Returns the values that were applied (for logging, keys only).
    """
    applied: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return applied
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"").strip()
        if (
            key
            and key not in os.environ
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
        ):
            os.environ[key] = value
            applied[key] = value
    return applied


class DeployError(Exception):
    """Fatal deploy failure (SSH error, missing files, ...)."""


@dataclass
class Comparison:
    """Result of comparing the local tree with the HA instance."""

    changed_backend: list[str] = field(default_factory=list)
    tree_card_changed: bool = False
    www_card_changed: bool = False
    stale_remote: list[str] = field(default_factory=list)

    @property
    def card_changed(self) -> bool:
        return self.tree_card_changed or self.www_card_changed

    @property
    def in_sync(self) -> bool:
        return not self.changed_backend and not self.card_changed


def run_ssh(target: str, command: str, stdin: bytes | None = None) -> bytes:
    """Run a remote command, return stdout. Raise DeployError on failure."""
    try:
        proc = subprocess.run(
            ["ssh", target, command],
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise DeployError(f"SSH to {target} failed: {err}") from err
    if proc.returncode != 0:
        raise DeployError(
            f"SSH to {target} failed (exit {proc.returncode}): "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_inventory() -> dict[str, str]:
    """Map repo-relative path (under custom_components/) -> sha256."""
    if not LOCAL_COMP.is_dir():
        raise DeployError(f"Local tree missing: {LOCAL_COMP}")
    inventory: dict[str, str] = {}
    for path in sorted(LOCAL_COMP.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in SKIP_SUFFIXES or any(
            part in SKIP_DIRS for part in path.parts
        ):
            continue
        rel = path.relative_to(REPO_ROOT / "custom_components").as_posix()
        inventory[rel] = sha256_file(path)
    if not inventory:
        raise DeployError("No local files found to deploy.")
    return inventory


def remote_inventory(
    target: str, rel_paths: list[str]
) -> tuple[dict, str | None, list]:
    """Fetch remote hashes in one SSH call.

    Returns (hashes, www_card_hash, stale_remote_paths).
    """
    rel_blob = "\n".join(rel_paths)
    script = (
        f"if [ -f '{CARD_WWW}' ]; then "
        f"( cd '{REMOTE_WWW_DIR}' && sha256sum dwellant-packages-card.js "
        f"| sed 's/ dwellant-packages-card.js/ WWW:card/' ); "
        f"else echo 'MISSING WWW:card'; fi; "
        f"if cd '{REMOTE_COMP}' 2>/dev/null; then "
        f"while IFS= read -r f; do "
        f'if [ -f "$f" ]; then sha256sum "$f"; '
        f'else echo "MISSING $f"; fi; done; '
        f"find . -type f ! -path '*__pycache__*' ! -name '*.pyc' "
        f"| sort | sed 's|^\\./|REMOTE_ONLY |'; "
        f"else echo 'NO_REMOTE_TREE'; fi"
    )
    out = run_ssh(target, script, stdin=rel_blob.encode()).decode()
    if not out.strip():
        raise DeployError(f"Empty response from {target}.")

    hashes: dict[str, str] = {}
    www_hash: str | None = None
    stale: list[str] = []
    no_tree = False
    for line in out.splitlines():
        if line == "NO_REMOTE_TREE":
            no_tree = True
            continue
        if line.startswith("REMOTE_ONLY "):
            stale.append(line[len("REMOTE_ONLY ") :])
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        if name == "WWW:card":
            www_hash = None if digest == "MISSING" else digest
        elif digest == "MISSING":
            hashes[name] = ""
        else:
            hashes[name] = digest
    local_names = set(rel_paths)
    return (
        hashes,
        www_hash,
        [s for s in stale if s not in local_names and f"{COMP}/{s}" not in local_names],
        no_tree,
    )


def compare(target: str, local: dict[str, str]) -> Comparison:
    """Diff local inventory against the HA instance."""
    rel_paths = sorted(local)
    remote, www_hash, stale, no_tree = remote_inventory(target, rel_paths)
    result = Comparison(stale_remote=sorted(stale))
    if no_tree:
        result.changed_backend = [p for p in rel_paths if p != CARD_REL]
        result.tree_card_changed = CARD_REL in local
    else:
        for path in rel_paths:
            if path == CARD_REL:
                if remote.get(path, "") != local[path]:
                    result.tree_card_changed = True
            elif remote.get(path, "") != local[path]:
                result.changed_backend.append(path)
    result.www_card_changed = www_hash != local[CARD_REL]
    return result


def report(target: str, result: Comparison) -> None:
    """Print the comparison outcome."""
    print(f"==> Comparing local tree with {target}")
    if result.in_sync:
        print("Already in sync — nothing to deploy.")
    if result.changed_backend:
        print(f"Backend files changed ({len(result.changed_backend)}):")
        for path in result.changed_backend:
            print(f"  ~ {path}")
    if result.card_changed:
        print("Card changed: yes")
    if result.stale_remote:
        print("Stale file(s) on HA, missing locally (will NOT be deleted):")
        for path in result.stale_remote:
            print(f"  ! {path}")


def deploy_backend(target: str, paths: list[str]) -> None:
    """Stream changed backend files to HA via tar (no pycache)."""
    run_ssh(
        target,
        f"mkdir -p '{REMOTE_COMP}/lovelace' '{REMOTE_COMP}/translations' "
        f"'{REMOTE_WWW_DIR}'",
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for rel in paths:
            full = REPO_ROOT / "custom_components" / rel
            info = tar.gettarinfo(str(full), arcname=rel)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with full.open("rb") as handle:
                tar.addfile(info, handle)
    run_ssh(target, f"tar -xf - -C config/custom_components", stdin=buffer.getvalue())


def ha_api_base() -> str:
    """HA base URL from .env/env (no hardcoded host). Empty when unset."""
    return (os.environ.get("HA_BASE_URL") or "").strip().rstrip("/")


def raise_restart_issue(base_url: str, token: str | None) -> str:
    """Raise the restart-required Repairs issue via HA API from this machine.

    Calls the integration's dwellant_packages.raise_restart_issue service
    over HTTPS (urllib, stdlib only). Returns "raised", "no-token",
    "no-service" (integration not loaded yet — first deploy ships the
    service itself), or "failed".
    """
    if not token or not base_url:
        return "no-token"
    import json
    import urllib.request

    payload = json.dumps({}).encode()
    request = urllib.request.Request(
        f"{base_url}/api/services/dwellant_packages/raise_restart_issue",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            code = resp.status
    except Exception:  # noqa: BLE001 - network/HTTP errors -> failed
        return "failed"
    if code in (200, 201):
        return "raised"
    if code == 404:
        return "no-service"
    return "failed"


def deploy_card(target: str) -> None:
    """Copy the Lovelace card to config/www via scp."""
    run_ssh(target, f"mkdir -p '{REMOTE_WWW_DIR}'")
    proc = subprocess.run(
        ["scp", str(CARD_LOCAL), f"{target}:{CARD_WWW}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if proc.returncode != 0:
        raise DeployError(
            "Card copy failed: " + proc.stderr.decode(errors="replace").strip()
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy dwellant_packages to HA (HACS model: never auto-restarts)."
    )
    parser.add_argument(
        "--check",
        "--dry-run",
        dest="check",
        action="store_true",
        help="compare with HA and report; deploy nothing",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="actually restart HA after deploying backend changes",
    )
    parser.add_argument(
        "--no-restart",
        dest="no_restart",
        action="store_true",
        help="never restart, only print the restart notice",
    )
    parser.add_argument(
        "--card-only",
        action="store_true",
        help="deploy only the Lovelace card (no restart)",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=os.environ.get("TARGET", "homeassistant"),
        help="SSH target (default: homeassistant or $TARGET)",
    )
    return parser.parse_args(argv)


def decide_restart(
    args: argparse.Namespace, deployed_backend: bool, deployed_card: bool
) -> str:
    """Return 'restart', 'notice', or 'none'."""
    if args.restart and (deployed_backend or deployed_card):
        return "restart"
    if args.no_restart:
        return "notice" if deployed_backend else "none"
    # Default (HACS model): notify, don't restart.
    if deployed_backend:
        return "notice"
    return "none"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    load_dotenv()
    try:
        local = local_inventory()
        result = compare(args.target, local)
    except DeployError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    report(args.target, result)

    if result.in_sync:
        return 0
    if args.check:
        if result.changed_backend:
            print("Decision: would deploy backend (restart notice, no restart).")
        else:
            print("Decision: would deploy card only (no restart).")
        return 0

    deployed_backend = deployed_card = False
    try:
        if not args.card_only:
            to_copy = list(result.changed_backend)
            if result.tree_card_changed:
                to_copy.append(CARD_REL)
            if to_copy:
                print(f"--> Copying {len(to_copy)} backend file(s)")
                deploy_backend(args.target, to_copy)
                deployed_backend = True
        elif result.changed_backend:
            print(
                f"WARNING: skipping {len(result.changed_backend)} "
                "backend file(s) (--card-only)."
            )
        if result.card_changed and (not args.card_only or True):
            if not args.card_only or result.www_card_changed:
                print(f"--> Copying Lovelace card to {CARD_WWW}")
                deploy_card(args.target)
                deployed_card = True
    except DeployError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    action = decide_restart(args, deployed_backend, deployed_card)
    if action == "restart":
        print("--> Restarting Home Assistant (real restart, ~30-60s downtime)")
        try:
            run_ssh(args.target, "ha core restart")
        except DeployError as err:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
        print("Done. Add the card per user:")
        print("  type: custom:dwellant-packages-card")
        print("  entity: sensor.dwellant_<email>")
    elif action == "notice":
        base_url = ha_api_base()
        token = os.environ.get("HA_TOKEN")
        status = raise_restart_issue(base_url, token)
        if status == "raised":
            print(
                "Done. Backend changed — a 'Restart required' Repairs issue "
                "was raised (Settings -> Repairs), with a one-click "
                "restart button."
            )
        else:
            if status == "no-service":
                print(
                    "Done. Backend changed — the Repairs service isn't "
                    "loaded yet (this deploy ships it; HA needs one manual "
                    "restart to pick it up). Future deploys will raise the "
                    "Repairs issue automatically. Restart manually:"
                )
            elif status == "failed":
                print(
                    "Done. Backend changed — could not reach the HA API at "
                    f"{base_url or '(HA_BASE_URL unset)'}. Check HA_BASE_URL "
                    "in .env and restart manually:"
                )
            else:  # no-token
                print(
                    "Done. Backend changed — restart HA to apply (HACS "
                    "model: not restarted automatically). Set HA_BASE_URL "
                    "and HA_TOKEN in .env to raise a Repairs issue "
                    "automatically, or restart manually:"
                )
            print(f"  ssh {args.target} 'ha core restart'")
    elif args.card_only and result.changed_backend:
        print("Done (card deployed; backend changes left pending for a full deploy).")
    else:
        print("Done (card only — no restart needed).")
        print("Hard-refresh your browser (Ctrl/Cmd+Shift+R) to pick up the new card.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
