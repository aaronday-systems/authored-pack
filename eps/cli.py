from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .binmode import stamp_from_entropy_bin
from .manifest import DEFAULT_DERIVATION_VERSION, stable_dumps
from .pack import StampResult, _output_would_self_ingest_input, stamp_pack, verify_pack

CLI_DESCRIPTION = (
    "Entropy Pack Stamper (EPS) stamps operator-supplied entropy-bearing file sets into deterministic, "
    "verifiable packs. It can derive reproducible seed material from rooted pack state and emits receipts "
    "that other tools can consume."
)

CLI_EPILOG = """\
First clean success:
  eps stamp --input /ABS/PATH/TO/DIR --out ./out --zip
  eps verify --pack ./out/<pack_root_sha256>

Then choose the path that fits the moment:
  busy humans -> stamp, then verify
  nervous humans -> stage sources in the TUI, then stamp
  machines -> eps stamp-bin --json

Trust boundary:
  use OS randomness for ordinary secret generation
  EPS is for auditable deterministic packaging and verification, not RNG
"""


class CliUsageError(ValueError):
    pass


class EPSArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _json_success(command: str, result: object) -> str:
    return stable_dumps({"ok": True, "command": command, "result": result})


def _json_failure(command: str, error_type: str, message: str) -> str:
    return stable_dumps(
        {
            "ok": False,
            "command": command,
            "error": {
                "type": error_type,
                "message": message,
            },
        }
    )


def _parse_dice(items: Sequence[str]) -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    for raw in items:
        s = str(raw).strip()
        if "=" not in s:
            raise ValueError(f"invalid dice (expected dN=value): {s!r}")
        die, val = s.split("=", 1)
        die = die.strip()
        val = val.strip()
        if not die:
            raise ValueError(f"invalid dice die: {s!r}")
        try:
            ival = int(val, 10)
        except ValueError as exc:
            raise ValueError(f"invalid dice value: {s!r}") from exc
        out.append((die, ival))
    return out


def _stamp(args: argparse.Namespace) -> int:
    dice = None
    if args.dice:
        dice = _parse_dice(args.dice)
    if args.json and args.print_seed:
        raise ValueError("--json cannot be combined with --print-seed")

    input_dir = Path(args.input)
    out_dir = Path(args.out)
    if _output_would_self_ingest_input(input_dir.expanduser().resolve(), out_dir.expanduser().resolve()):
        raise ValueError("--input and --out must not overlap")

    res: StampResult = stamp_pack(
        input_dir=input_dir,
        out_dir=out_dir,
        pack_id=args.pack_id,
        notes=args.notes,
        created_at_utc=args.created_at_utc,
        dice=dice,
        include_hidden=bool(args.include_hidden),
        zip_pack=bool(args.zip),
        derive_seed=bool(args.derive_seed),
        evidence_bundle=bool(args.evidence_bundle),
        write_seed_files=bool(args.write_seed),
        print_seed=bool(args.print_seed),
    )

    if args.json:
        payload = {
            "pack_dir": str(res.pack_dir),
            "pack_root_sha256": res.pack_root_sha256,
            "entropy_root_sha256": res.root_sha256,
            "payload_root_sha256": res.payload_root_sha256,
            "receipt": res.receipt,
        }
        if res.zip_path is not None:
            payload["zip_path"] = str(res.zip_path)
        if res.evidence_bundle_path is not None:
            payload["evidence_bundle_path"] = str(res.evidence_bundle_path)
        if res.evidence_bundle_sha256:
            payload["evidence_bundle_sha256"] = res.evidence_bundle_sha256
        print(_json_success("stamp", payload))
    else:
        print(f"pack_dir: {res.pack_dir}")
        print(f"pack_root_sha256: {res.pack_root_sha256}")
        print(f"payload_root_sha256: {res.payload_root_sha256}")
        if args.derive_seed:
            fp = res.receipt.get("derived_seed_fingerprint_sha256") or res.receipt.get("seed_fingerprint_sha256")
            if isinstance(fp, str) and fp:
                print(f"derived_seed_fingerprint_sha256: {fp}")
        if res.evidence_bundle_sha256:
            print(f"evidence_bundle_sha256: {res.evidence_bundle_sha256}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    max_manifest_bytes = int(args.max_manifest_mib) * 1024 * 1024
    res = verify_pack(Path(args.pack), max_manifest_bytes=max_manifest_bytes)
    if args.json:
        if not res.ok:
            msg = res.errors[0] if res.errors else "verification failed"
            print(_json_failure("verify", "VerificationError", msg))
            return 1
        payload = {
            "pack_root_sha256": res.root_sha256,
            "entropy_root_sha256": res.root_sha256,
            "payload_root_sha256": res.payload_root_sha256,
            "artifact_count_verified": res.file_count,
            "artifact_bytes_verified": res.total_bytes,
            "errors": list(res.errors),
            "limits": {"max_manifest_mib": int(args.max_manifest_mib)},
        }
        print(_json_success("verify", payload))
    else:
        if res.ok:
            print("ok")
            print(f"pack_root_sha256: {res.root_sha256}")
            if res.payload_root_sha256:
                print(f"payload_root_sha256: {res.payload_root_sha256}")
            print(f"artifact_count_verified: {res.file_count}")
            print(f"artifact_bytes_verified: {res.total_bytes}")
        else:
            print("verify_failed", file=sys.stderr)
            for e in res.errors:
                print(f"- {e}", file=sys.stderr)
    return 0 if res.ok else 1


def _stamp_bin(args: argparse.Namespace) -> int:
    res = stamp_from_entropy_bin(
        entropy_bin=Path(args.entropy_bin),
        out_dir=Path(args.out),
        count=int(args.count),
        min_remaining=int(args.min_remaining),
        allow_low_bin=bool(args.allow_low_bin),
        recursive=bool(args.recursive),
        include_hidden=bool(args.include_hidden),
        zip_pack=bool(args.zip),
        derive_seed=bool(args.derive_seed),
        evidence_bundle=bool(args.evidence_bundle),
    )

    if args.json:
        payload = {
            "mode": "entropy_bin",
            "entropy_bin": str(res.entropy_bin),
            "bin_files_before": int(res.bin_files_before),
            "bin_files_after": int(res.bin_files_after),
            "consumed_count": len(res.consumed),
            "pack_dir": str(res.stamp.pack_dir),
            "pack_root_sha256": res.stamp.pack_root_sha256,
            "entropy_root_sha256": res.stamp.root_sha256,
            "payload_root_sha256": res.stamp.payload_root_sha256,
            "receipt": res.stamp.receipt,
        }
        if res.stamp.zip_path is not None:
            payload["zip_path"] = str(res.stamp.zip_path)
        if res.stamp.evidence_bundle_path is not None:
            payload["evidence_bundle_path"] = str(res.stamp.evidence_bundle_path)
        if res.stamp.evidence_bundle_sha256:
            payload["evidence_bundle_sha256"] = res.stamp.evidence_bundle_sha256
        print(_json_success("stamp-bin", payload))
        return 0

    # Human-readable.
    if (res.bin_files_before - int(args.count)) < int(args.min_remaining):
        print(
            f"warning: entropy bin low-watermark: {res.bin_files_before} files before, consuming {args.count}, "
            f"min_remaining={args.min_remaining}",
            file=sys.stderr,
        )
    print("mode: entropy_bin")
    print(f"entropy_bin: {res.entropy_bin}")
    print(f"bin_files_before: {res.bin_files_before}")
    print(f"bin_files_after: {res.bin_files_after}")
    print(f"consumed_count: {len(res.consumed)}")
    print(f"pack_dir: {res.stamp.pack_dir}")
    print(f"pack_root_sha256: {res.stamp.pack_root_sha256}")
    print(f"payload_root_sha256: {res.stamp.payload_root_sha256}")
    fp = res.stamp.receipt.get("derived_seed_fingerprint_sha256") or res.stamp.receipt.get("seed_fingerprint_sha256")
    if isinstance(fp, str) and fp:
        print(f"derived_seed_fingerprint_sha256: {fp}")
    if res.stamp.evidence_bundle_sha256:
        print(f"evidence_bundle_sha256: {res.stamp.evidence_bundle_sha256}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = EPSArgumentParser(
        prog="eps",
        description=CLI_DESCRIPTION,
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    stamp = sub.add_parser("stamp", help="Operator path: turn a directory into a deterministic verifiable pack")
    stamp.add_argument("--input", required=True, help="Directory of operator-supplied artifacts to include")
    stamp.add_argument("--out", required=True, help="Content-addressed output directory for the stamped pack")
    stamp.add_argument("--pack-id", default=None, help="Optional human label stored in manifest metadata (affects root)")
    stamp.add_argument("--notes", default=None, help="Optional notes stored in manifest metadata (affects root)")
    stamp.add_argument(
        "--created-at-utc",
        default=None,
        help="Optional ISO8601 UTC timestamp stored in manifest (affects root); omitted when unset",
    )
    stamp.add_argument(
        "--dice",
        action="append",
        default=[],
        help="Optional die roll like d6=4; repeatable; stored in manifest (affects root)",
    )
    stamp.add_argument("--include-hidden", action="store_true", help="Include dotfiles in input scan")
    stamp.add_argument("--zip", action="store_true", help="Write entropy_pack.zip alongside pack dir")

    stamp.add_argument("--derive-seed", action="store_true", help=f"Derive reproducible seed material via HKDF ({DEFAULT_DERIVATION_VERSION})")
    stamp.add_argument("--write-seed", action="store_true", help="Write derived seed material as seed_master.{hex,b64} (chmod 600 best-effort)")
    stamp.add_argument("--print-seed", action="store_true", help="Print derived seed material as seed_master.{hex,b64} to stdout (no files, incompatible with --json)")
    stamp.add_argument("--evidence-bundle", action="store_true", help="Write eps_evidence_<root>.zip + .sha256 (tamper-evident local audit bundle)")

    stamp.add_argument("--json", action="store_true", help="Emit receipt JSON to stdout")
    stamp.set_defaults(func=_stamp)

    verify = sub.add_parser("verify", help="Post-run audit: verify a stamped pack directory or .zip")
    verify.add_argument("--pack", required=True, help="Path to pack dir or entropy_pack.zip")
    verify.add_argument("--max-manifest-mib", type=int, default=4, help="Maximum manifest.json size to accept (default: 4)")
    verify.add_argument("--json", action="store_true", help="Emit verification JSON to stdout")
    verify.set_defaults(func=_verify)

    stamp_bin = sub.add_parser("stamp-bin", help="Machine sidecar: subtractively consume entropy-bin files and emit receipts")
    stamp_bin.add_argument(
        "--entropy-bin",
        default="./bins/entropy_bin",
        help="Directory containing entropy-bearing files to consume (moved, not copied) (default: ./bins/entropy_bin)",
    )
    stamp_bin.add_argument(
        "--out",
        default="./bins/eps_out",
        help="Output directory for stamped pack (content-addressed) (default: ./bins/eps_out)",
    )
    stamp_bin.add_argument("--count", type=int, default=7, help="How many files to consume and stamp (default: 7)")
    stamp_bin.add_argument("--min-remaining", type=int, default=50, help="Refuse if remaining after consumption would be below this (default: 50)")
    stamp_bin.add_argument("--allow-low-bin", action="store_true", help="Proceed even if low-watermark would be violated (prints warning)")
    stamp_bin.add_argument("--recursive", action="store_true", help="Scan entropy bin recursively (default)")
    stamp_bin.add_argument("--no-recursive", dest="recursive", action="store_false", help="Only scan top-level of entropy bin")
    stamp_bin.set_defaults(recursive=True)
    stamp_bin.add_argument("--include-hidden", action="store_true", help="Include dotfiles while scanning entropy bin")
    # Push-button defaults: on.
    stamp_bin.add_argument("--zip", action=argparse.BooleanOptionalAction, default=True, help="Write entropy_pack.zip alongside pack dir (default: on)")
    stamp_bin.add_argument(
        "--derive-seed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=f"Derive reproducible seed material via HKDF ({DEFAULT_DERIVATION_VERSION}) (default: on)",
    )
    stamp_bin.add_argument(
        "--evidence-bundle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write eps_evidence_<root>.zip + .sha256 (tamper-evident) (default: on)",
    )
    stamp_bin.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    stamp_bin.set_defaults(func=_stamp_bin)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv_list = list(argv) if argv is not None else list(sys.argv[1:])
    json_mode = "--json" in argv_list
    parser = build_parser()
    try:
        ns = parser.parse_args(argv_list)
        if not hasattr(ns, "func"):
            parser.print_help()
            return 2
        return int(ns.func(ns))
    except (CliUsageError, ValueError, FileExistsError, RuntimeError, OSError) as exc:
        # Keep CLI UX clean: most user-caused validation failures should not
        # show a Python traceback.
        msg = str(exc).strip() or exc.__class__.__name__
        if json_mode:
            command = "eps"
            if argv_list:
                first = str(argv_list[0]).strip()
                if first and not first.startswith("-"):
                    command = first
            print(_json_failure(command, exc.__class__.__name__, msg))
            return 1
        print(f"eps: error: {msg}", file=sys.stderr)
        if isinstance(exc, ValueError) and "must be a directory" in msg and "--input" in msg:
            print("hint: pass an existing directory path; many terminals let you drag a folder into the window to paste its absolute path.", file=sys.stderr)
        return 2 if isinstance(exc, ValueError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
