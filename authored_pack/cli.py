from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import __product_name__, __version__
from .binmode import stamp_from_source_bin
from .manifest import DEFAULT_DERIVATION_VERSION, stable_dumps
from .pack import StampResult, _output_would_self_ingest_input, inspect_pack, stamp_pack, verify_pack

CLI_DESCRIPTION = (
    f"{__product_name__} is a small deterministic pack/verify tool for humans and agents. "
    "It turns a directory into a verifiable pack with a manifest and receipt, and can optionally derive "
    "reproducible seed material from the pack root."
)

CLI_EPILOG = """\
First clean success:
  authored-pack stamp --input /ABS/PATH/TO/DIR --out ./out --zip
  authored-pack verify --pack ./out/<pack_root_sha256>

Human path:
  python3 -B bin/authored_pack.py
  stage sources if you need them, then stamp and verify

Machine path:
  authored-pack stamp --input /ABS/PATH/TO/DIR --out ./out --json
  stamp-bin is subtractive and uses repo-relative defaults

Trust boundary:
  use OS randomness for ordinary secret generation
  Authored Pack is not an RNG, not automatic secrecy, not signed provenance,
  and not sealed storage
"""


class CliUsageError(ValueError):
    pass


class CliCommandError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: Optional[str] = None,
        details: Optional[Dict[str, object]] = None,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.error_type = str(error_type or self.__class__.__name__)
        self.details = dict(details) if details else None
        self.exit_code = int(exit_code)


class EPSArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _json_success(command: str, result: object) -> str:
    return stable_dumps({"ok": True, "command": command, "result": result})


def _json_failure(command: str, error_type: str, message: str, *, details: Optional[Dict[str, object]] = None) -> str:
    error: Dict[str, object] = {
        "type": error_type,
        "message": message,
    }
    if details:
        error["details"] = details
    return stable_dumps({"ok": False, "command": command, "error": error})


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


def _validate_seed_flags(args: argparse.Namespace) -> None:
    if args.json and args.print_seed:
        raise ValueError("--json cannot be combined with --print-seed")
    if args.write_seed and not args.derive_seed:
        raise ValueError("--write-seed requires --derive-seed")
    if args.print_seed and not args.derive_seed:
        raise ValueError("--print-seed requires --derive-seed")


def _command_name_from_argv(argv_list: Sequence[str], *, default_command: str = "authored-pack") -> str:
    known = {"stamp", "verify", "inspect", "stamp-bin"}
    for item in argv_list:
        token = str(item).strip()
        if token in known:
            return token
    return default_command


def _prog_name_from_argv0(argv0: str) -> str:
    name = Path(argv0).name
    stem = Path(name).stem
    if stem in {"authored-pack", "authored_pack"}:
        return "authored-pack"
    return "authored-pack"


def _stamp(args: argparse.Namespace) -> int:
    dice = None
    if args.dice:
        dice = _parse_dice(args.dice)
    _validate_seed_flags(args)

    input_dir = Path(args.input)
    out_dir = Path(args.out)
    if _output_would_self_ingest_input(input_dir.expanduser().resolve(), out_dir.expanduser().resolve()):
        raise ValueError("--input and --out must not overlap in either direction")

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
        if res.zip_path is not None:
            print(f"zip_path: {res.zip_path}")
        if args.derive_seed:
            fp = res.receipt.get("derived_seed_fingerprint_sha256")
            if isinstance(fp, str) and fp:
                print(f"derived_seed_fingerprint_sha256: {fp}")
        if res.evidence_bundle_path is not None:
            print(f"evidence_bundle_path: {res.evidence_bundle_path}")
        if res.evidence_bundle_sha256:
            print(f"evidence_bundle_sha256: {res.evidence_bundle_sha256}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    max_manifest_bytes = int(args.max_manifest_mib) * 1024 * 1024
    res = verify_pack(Path(args.pack), max_manifest_bytes=max_manifest_bytes)
    if not res.ok:
        msg = res.errors[0] if res.errors else "verification failed"
        raise CliCommandError(
            msg,
            error_type="VerificationError",
            details={
                "pack": str(Path(args.pack)),
                "errors": list(res.errors),
                "limits": {"max_manifest_mib": int(args.max_manifest_mib)},
            },
            exit_code=1,
        )

    if args.json:
        payload = {
            "pack_root_sha256": res.root_sha256,
            "payload_root_sha256": res.payload_root_sha256,
            "artifact_count_verified": res.file_count,
            "artifact_bytes_verified": res.total_bytes,
            "errors": list(res.errors),
            "limits": {"max_manifest_mib": int(args.max_manifest_mib)},
        }
        print(_json_success("verify", payload))
    else:
        print("ok")
        print(f"pack_root_sha256: {res.root_sha256}")
        if res.payload_root_sha256:
            print(f"payload_root_sha256: {res.payload_root_sha256}")
        print(f"artifact_count_verified: {res.file_count}")
        print(f"artifact_bytes_verified: {res.total_bytes}")
    return 0


def _inspect(args: argparse.Namespace) -> int:
    max_manifest_bytes = int(args.max_manifest_mib) * 1024 * 1024
    summary = inspect_pack(
        Path(args.pack),
        max_manifest_bytes=max_manifest_bytes,
        artifact_preview_limit=int(args.artifact_preview),
    )

    if args.json:
        print(_json_success("inspect", summary))
    else:
        print(f"inspected_path: {summary['inspected_path']}")
        print(f"pack_type: {summary['pack_type']}")
        print(f"pack_root_sha256: {summary['pack_root_sha256']}")
        payload_root = str(summary.get("payload_root_sha256", "") or "")
        if payload_root:
            print(f"payload_root_sha256: {payload_root}")
        print(f"manifest_schema_version: {summary['manifest_schema_version']}")
        print(f"artifact_count: {summary['artifact_count']}")
        print(f"artifact_bytes: {summary['artifact_bytes']}")
        print(f"verification_ok: {str(bool(summary['verification_ok'])).lower()}")
        if summary.get("receipt_summary"):
            receipt_summary = summary["receipt_summary"]
            if isinstance(receipt_summary, dict):
                schema = receipt_summary.get("schema_version")
                if schema:
                    print(f"receipt_schema_version: {schema}")
                layout = receipt_summary.get("pack_layout")
                if layout:
                    print(f"pack_layout: {layout}")
        preview = summary.get("artifact_preview")
        if isinstance(preview, list) and preview:
            print("artifact_preview:")
            for item in preview:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path", ""))
                size = item.get("size_bytes")
                print(f"- {path} ({size} bytes)" if isinstance(size, int) else f"- {path}")
        errors = summary.get("verification_errors")
        if isinstance(errors, list) and errors:
            print("verification_errors:")
            for err in errors:
                print(f"- {err}")
    return 0


def _stamp_bin(args: argparse.Namespace) -> int:
    res = stamp_from_source_bin(
        source_bin=Path(args.source_bin),
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
    projected_after = int(res.bin_files_before) - int(args.count)
    low_watermark_violation = projected_after < int(args.min_remaining)
    warnings: List[str] = []
    if low_watermark_violation:
        warnings.append(
            f"source bin low-watermark: {res.bin_files_before} files before, consuming {args.count}, "
            f"min_remaining={args.min_remaining}"
        )

    if args.json:
        payload = {
            "mode": "source_bin",
            "source_bin": str(res.source_bin),
            "bin_files_before": int(res.bin_files_before),
            "bin_files_after": int(res.bin_files_after),
            "consumed_count": len(res.consumed),
            "consumed": [
                {
                    "src_path": str(item.src_path),
                    "src_relpath": item.src_path.relative_to(res.source_bin).as_posix(),
                    "staged_name": item.staged_path.name,
                }
                for item in res.consumed
            ],
            "warnings": warnings,
            "policy": {
                "count": int(args.count),
                "min_remaining": int(args.min_remaining),
                "allow_low_bin": bool(args.allow_low_bin),
                "projected_remaining_after_count": int(projected_after),
                "would_violate_low_watermark": bool(low_watermark_violation),
            },
            "pack_dir": str(res.stamp.pack_dir),
            "pack_root_sha256": res.stamp.pack_root_sha256,
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
    if low_watermark_violation:
        print(
            f"warning: {warnings[0]}",
            file=sys.stderr,
        )
    print("mode: source_bin")
    print(f"source_bin: {res.source_bin}")
    print(f"bin_files_before: {res.bin_files_before}")
    print(f"bin_files_after: {res.bin_files_after}")
    print(f"consumed_count: {len(res.consumed)}")
    print(f"pack_dir: {res.stamp.pack_dir}")
    print(f"pack_root_sha256: {res.stamp.pack_root_sha256}")
    print(f"payload_root_sha256: {res.stamp.payload_root_sha256}")
    fp = res.stamp.receipt.get("derived_seed_fingerprint_sha256")
    if isinstance(fp, str) and fp:
        print(f"derived_seed_fingerprint_sha256: {fp}")
    if res.stamp.evidence_bundle_sha256:
        print(f"evidence_bundle_sha256: {res.stamp.evidence_bundle_sha256}")
    return 0


def build_parser(*, prog: str = "authored-pack") -> argparse.ArgumentParser:
    p = EPSArgumentParser(
        prog=prog,
        description=CLI_DESCRIPTION,
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    stamp = sub.add_parser("stamp", help="Operator path: turn a directory into a deterministic verifiable pack")
    stamp.add_argument("--input", required=True, help="Directory of artifacts to include")
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
    stamp.add_argument("--zip", action="store_true", help="Write authored_pack.zip alongside pack dir")

    stamp.add_argument("--derive-seed", action="store_true", help=f"Derive reproducible seed material via HKDF ({DEFAULT_DERIVATION_VERSION})")
    stamp.add_argument("--write-seed", action="store_true", help="Write derived seed material as seed_master.{hex,b64} (requires --derive-seed)")
    stamp.add_argument("--print-seed", action="store_true", help="Print derived seed material as seed_master.{hex,b64} to stdout (requires --derive-seed, incompatible with --json)")
    stamp.add_argument("--evidence-bundle", action="store_true", help="Write authored_evidence_<root>.zip + .sha256 (tamper-evident local audit bundle)")

    stamp.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    stamp.set_defaults(func=_stamp)

    verify = sub.add_parser("verify", help="Post-run audit: verify a stamped pack directory or .zip")
    verify.add_argument("--pack", required=True, help="Path to pack dir or authored_pack.zip")
    verify.add_argument("--max-manifest-mib", type=int, default=4, help="Maximum manifest.json size to accept (default: 4)")
    verify.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    verify.set_defaults(func=_verify)

    inspect = sub.add_parser("inspect", help="Summarize a stamped pack directory or .zip for machine or human review")
    inspect.add_argument("--pack", required=True, help="Path to pack dir or authored_pack.zip")
    inspect.add_argument("--max-manifest-mib", type=int, default=4, help="Maximum manifest.json size to accept while inspecting (default: 4)")
    inspect.add_argument("--artifact-preview", type=int, default=20, help="How many artifact entries to include in the summary preview (default: 20)")
    inspect.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    inspect.set_defaults(func=_inspect)

    stamp_bin = sub.add_parser("stamp-bin", help="Machine sidecar: subtractively consume source-bin files and emit receipts")
    stamp_bin.add_argument(
        "--source-bin",
        default="./bins/source_bin",
        help="Directory containing source files to consume (moved, not copied) (default: ./bins/source_bin)",
    )
    stamp_bin.add_argument(
        "--out",
        default="./bins/authored_out",
        help="Output directory for stamped pack (content-addressed) (default: ./bins/authored_out)",
    )
    stamp_bin.add_argument("--count", type=int, default=7, help="How many files to consume and stamp (default: 7)")
    stamp_bin.add_argument("--min-remaining", type=int, default=50, help="Refuse if remaining after consumption would be below this (default: 50)")
    stamp_bin.add_argument("--allow-low-bin", action="store_true", help="Proceed even if low-watermark would be violated (prints warning)")
    stamp_bin.add_argument("--recursive", action="store_true", help="Scan source bin recursively (default)")
    stamp_bin.add_argument("--no-recursive", dest="recursive", action="store_false", help="Only scan top-level of source bin")
    stamp_bin.set_defaults(recursive=True)
    stamp_bin.add_argument("--include-hidden", action="store_true", help="Include dotfiles while scanning source bin")
    # Push-button defaults: on.
    stamp_bin.add_argument("--zip", action=argparse.BooleanOptionalAction, default=True, help="Write authored_pack.zip alongside pack dir (default: on)")
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
        help="Write authored_evidence_<root>.zip + .sha256 (tamper-evident) (default: on)",
    )
    stamp_bin.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    stamp_bin.set_defaults(func=_stamp_bin)

    return p


def main(argv: Optional[Sequence[str]] = None, *, prog: Optional[str] = None) -> int:
    if argv is None:
        argv_list = list(sys.argv[1:])
        prog_name = str(prog or _prog_name_from_argv0(sys.argv[0]))
    else:
        argv_list = list(argv)
        prog_name = str(prog or "authored-pack")
    json_mode = "--json" in argv_list
    parser = build_parser(prog=prog_name)
    if not argv_list:
        parser.print_help()
        return 0
    if argv_list in (["--version"], ["-V"]):
        print(f"{prog_name} {__version__}")
        return 0
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
            command = _command_name_from_argv(argv_list, default_command=prog_name)
            error_type = exc.__class__.__name__
            details: Optional[Dict[str, object]] = None
            if isinstance(exc, CliCommandError):
                error_type = exc.error_type
                details = exc.details
            print(_json_failure(command, error_type, msg, details=details))
            return 1
        print(f"{prog_name}: error: {msg}", file=sys.stderr)
        if isinstance(exc, ValueError) and "must be a directory" in msg and "--input" in msg:
            print("hint: pass an existing directory path; many terminals let you drag a folder into the window to paste its absolute path.", file=sys.stderr)
        if isinstance(exc, CliCommandError):
            return int(exc.exit_code)
        return 2 if isinstance(exc, ValueError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
