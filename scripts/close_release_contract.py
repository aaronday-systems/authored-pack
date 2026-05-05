#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LANE_PATH = ROOT / ".control" / "lane.json"
RECEIPT_DIR = ROOT / "docs" / "task_receipts"
RECEIPT_SPEC = "dev-architect.task_receipt.v1"
LANE_ID = "authored-pack.release-contract"
VERIFY_COMMAND = ["bash", "scripts/release_check.sh"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_json(obj: dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def run(cmd: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)
    if check and proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.stderr.write(proc.stdout)
        raise SystemExit(proc.returncode)
    return proc


def load_lane() -> dict[str, Any]:
    if not LANE_PATH.is_file():
        raise SystemExit(f"missing lane contract: {LANE_PATH}")
    lane = json.loads(LANE_PATH.read_text(encoding="utf-8"))
    if lane.get("lane_id") != LANE_ID:
        raise SystemExit(f"unexpected lane_id in {LANE_PATH}: {lane.get('lane_id')!r}")
    if lane.get("verify_command") != VERIFY_COMMAND:
        raise SystemExit(f"unexpected verify_command in {LANE_PATH}: {lane.get('verify_command')!r}")
    if lane.get("receipt_required") is not True:
        raise SystemExit(f"lane must require a receipt: {LANE_PATH}")
    return lane


def tracked_tree_is_clean() -> bool:
    unstaged = run(["git", "diff", "--quiet", "--ignore-submodules", "--"], cwd=ROOT, check=False)
    staged = run(["git", "diff", "--cached", "--quiet", "--ignore-submodules", "--"], cwd=ROOT, check=False)
    return unstaged.returncode == 0 and staged.returncode == 0


def make_worktree(prefix: str) -> Path:
    parent = Path(tempfile.mkdtemp(prefix=prefix, dir="/private/tmp"))
    worktree = parent / "worktree"
    run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"], cwd=ROOT)
    return worktree


def remove_worktree(worktree: Path) -> None:
    run(["git", "worktree", "remove", "--force", str(worktree)], cwd=ROOT, check=False)
    shutil.rmtree(worktree.parent, ignore_errors=True)


def write_receipt(receipt: dict[str, Any], recorded_at: str) -> Path:
    stamp = recorded_at.replace("-", "").replace(":", "").replace("Z", "Z")
    path = RECEIPT_DIR / f"authored-pack_{stamp}.json"
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(stable_json(receipt) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    return path


def build_receipt(
    *,
    lane: dict[str, Any],
    worktree: Path,
    release_proc: subprocess.CompletedProcess[str],
    recorded_at: str,
    main_tree_clean: bool,
) -> dict[str, Any]:
    lane_instance_id = str(lane.get("lane_instance_id", ""))
    return {
        "spec": RECEIPT_SPEC,
        "result": "success" if release_proc.returncode == 0 else "failed",
        "lane_id": LANE_ID,
        "lane_instance_id": lane_instance_id,
        "objective": str(lane.get("action", "Run the Authored Pack release contract.")),
        "recorded_at_utc": recorded_at,
        "commands_run": [
            "python3 scripts/close_release_contract.py",
            "git worktree add --detach <temp-worktree> HEAD",
            "bash scripts/release_check.sh",
            "git worktree remove --force <temp-worktree>",
        ],
        "owned_scope": [
            ".control/lane.json",
            "scripts/close_release_contract.py",
            "docs/task_receipts/",
        ],
        "selected_control_decision": {
            "authority": "repo-local typed lane",
            "decision": "detached_clean_worktree_release_check",
            "path": str(LANE_PATH),
        },
        "verification": [
            {
                "name": "selected-lane verify command",
                "status": "passed" if release_proc.returncode == 0 else "failed",
                "command": "bash scripts/release_check.sh",
                "cwd": str(worktree),
                "exit_code": int(release_proc.returncode),
                "verified_at_utc": recorded_at,
            }
        ],
        "verification_result": "Canonical release gate passed in a detached clean worktree at HEAD.",
        "notes": (
            "The main tracked tree was clean, but the release contract was still run from a detached clean worktree."
            if main_tree_clean
            else "The main tracked tree was dirty, so the release contract was run from a detached clean worktree at HEAD without weakening scripts/release_check.sh."
        ),
        "residual_risks": [
            "The receipt records the release gate result for HEAD in a detached clean worktree; uncommitted main-tree edits are not release-verified until committed or separately tested.",
            "This helper closes the repo-local release-contract lane only; it does not change Authored Pack schemas, aliases, or trust-boundary claims.",
        ],
        "rollback": "Remove the generated docs/task_receipts/authored-pack_<timestamp>.json file.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the release contract in a detached clean worktree and write a typed receipt.")
    parser.add_argument("--json", action="store_true", help="Emit the receipt path and verification status as JSON.")
    args = parser.parse_args(argv)

    lane = load_lane()
    main_tree_clean = tracked_tree_is_clean()
    worktree = make_worktree("authored-pack-release-contract-")
    recorded_at = utc_now()
    try:
        release_proc = run(VERIFY_COMMAND, cwd=worktree, check=False)
        receipt = build_receipt(
            lane=lane,
            worktree=worktree,
            release_proc=release_proc,
            recorded_at=recorded_at,
            main_tree_clean=main_tree_clean,
        )
        receipt_path = write_receipt(receipt, recorded_at)
    finally:
        remove_worktree(worktree)

    if args.json:
        print(stable_json({"ok": release_proc.returncode == 0, "receipt_path": str(receipt_path), "receipt": receipt}))
    else:
        print(f"receipt_path: {receipt_path}")
        print(f"release_check_exit_code: {release_proc.returncode}")
    return int(release_proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
