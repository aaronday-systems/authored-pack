from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import __product_name__, __version__
from .binmode import consume_from_source_bin
from .manifest import DEFAULT_DERIVATION_VERSION, stable_dumps
from .pack import (
    DEFAULT_MAX_ARTIFACT_BYTES,
    DEFAULT_MAX_MANIFEST_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
    AssembleResult,
    _output_would_self_ingest_input,
    inspect_pack,
    assemble_pack,
    verify_pack,
)

CLI_DESCRIPTION = (
    f"{__product_name__} assembles a folder into a deterministic pack you can verify or inspect later."
)
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_BIN = REPO_ROOT / "bins" / "source_bin"
DEFAULT_AUTHORED_OUT = REPO_ROOT / "bins" / "authored_out"
MIB = 1024 * 1024
DEFAULT_MAX_MANIFEST_MIB = DEFAULT_MAX_MANIFEST_BYTES // MIB
DEFAULT_MAX_ARTIFACT_MIB = DEFAULT_MAX_ARTIFACT_BYTES // MIB
DEFAULT_MAX_TOTAL_MIB = DEFAULT_MAX_TOTAL_BYTES // MIB

CLI_EPILOG = """\
Start here:
  python3 -m authored_pack assemble --input /ABS/PATH/TO/DIR --out ./out --zip
  python3 -m authored_pack verify --pack ./out/<pack_root_sha256>/authored_pack.zip
  python3 -m authored_pack inspect --pack ./out/<pack_root_sha256>/authored_pack.zip --json

Alternative:
  python3 -B bin/authored_pack.py

More:
  python3 -m authored_pack assemble --help
  python3 -m authored_pack consume-bin --help
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


def _hide_subcommand_aliases_in_help(subparsers: argparse._SubParsersAction) -> None:
    for action in getattr(subparsers, "_choices_actions", []):
        dest = getattr(action, "dest", None)
        if isinstance(dest, str) and dest:
            action.metavar = dest


def _value_error(message: str, *, details: Optional[Dict[str, object]] = None) -> CliCommandError:
    return CliCommandError(message, error_type="ValueError", details=details, exit_code=2)


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
        raise _value_error(
            "--json cannot be combined with --print-seed",
            details={"flags": {"json": True, "print_seed": True, "derive_seed": bool(args.derive_seed)}},
        )
    if args.write_seed and not args.derive_seed:
        raise _value_error(
            "--write-seed requires --derive-seed",
            details={"flags": {"write_seed": True, "derive_seed": False}},
        )
    if args.print_seed and not args.derive_seed:
        raise _value_error(
            "--print-seed requires --derive-seed",
            details={"flags": {"print_seed": True, "derive_seed": False}},
        )


def _command_name_from_argv(argv_list: Sequence[str], *, default_command: str = "authored-pack") -> str:
    known = {"assemble", "stamp", "verify", "inspect", "consume-bin", "stamp-bin"}
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


def _resolve_supported_pack_path(raw_pack: str) -> Path:
    pack_path = Path(raw_pack).expanduser().resolve()
    if pack_path.is_dir() or (pack_path.is_file() and pack_path.suffix.lower() == ".zip"):
        return pack_path
    raise _value_error(
        f"unsupported pack path: {pack_path}",
        details={
            "pack": str(pack_path),
            "reason": "unsupported pack path",
            "supported_pack_types": ["directory", "zip"],
        },
    )


def _ensure_repo_clone_consume_bin_defaults(source_bin: Path, out_dir: Path) -> None:
    using_default_source = source_bin == DEFAULT_SOURCE_BIN.resolve()
    using_default_out = out_dir == DEFAULT_AUTHORED_OUT.resolve()
    if not (using_default_source or using_default_out):
        return
    if DEFAULT_SOURCE_BIN.is_dir() and DEFAULT_AUTHORED_OUT.is_dir():
        return
    raise _value_error(
        "repo-local consume-bin defaults are unavailable here; pass explicit --source-bin and --out",
        details={
            "source_bin": str(source_bin),
            "out": str(out_dir),
            "reason": "repo-local defaults unavailable",
            "requires_explicit_paths": True,
            "repo_default_source_bin_exists": DEFAULT_SOURCE_BIN.is_dir(),
            "repo_default_out_exists": DEFAULT_AUTHORED_OUT.is_dir(),
        },
    )


def _verification_limits_mib(args: argparse.Namespace) -> Dict[str, int]:
    return {
        "max_manifest_mib": int(args.max_manifest_mib),
        "max_artifact_mib": int(args.max_artifact_mib),
        "max_total_mib": int(args.max_total_mib),
    }


def _verification_limits_bytes(args: argparse.Namespace) -> Tuple[int, int, int]:
    limits = _verification_limits_mib(args)
    return (
        limits["max_manifest_mib"] * MIB,
        limits["max_artifact_mib"] * MIB,
        limits["max_total_mib"] * MIB,
    )


def _validated_verification_limits(args: argparse.Namespace) -> Tuple[Dict[str, int], Tuple[int, int, int]]:
    limits = _verification_limits_mib(args)
    invalid = {name: value for name, value in limits.items() if int(value) <= 0}
    if invalid:
        raise _value_error(
            "verification limits must be positive integers",
            details={"reason": "invalid verify limits", "limits": limits, "invalid_limits": invalid},
        )
    return limits, _verification_limits_bytes(args)


def _assemble(args: argparse.Namespace) -> int:
    dice = None
    if args.dice:
        dice = _parse_dice(args.dice)
    _validate_seed_flags(args)

    input_dir = Path(args.input)
    out_dir = Path(args.out)
    if _output_would_self_ingest_input(input_dir.expanduser().resolve(), out_dir.expanduser().resolve()):
        raise _value_error(
            "--input and --out must not overlap in either direction",
            details={
                "input": str(input_dir.expanduser().resolve()),
                "out": str(out_dir.expanduser().resolve()),
                "reason": "paths overlap",
            },
        )

    res: AssembleResult = assemble_pack(
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
        print(_json_success(str(getattr(args, "cmd", "assemble")), payload))
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
    limits, (max_manifest_bytes, max_artifact_bytes, max_total_bytes) = _validated_verification_limits(args)
    pack_path = _resolve_supported_pack_path(args.pack)
    res = verify_pack(
        pack_path,
        max_manifest_bytes=max_manifest_bytes,
        max_artifact_bytes=max_artifact_bytes,
        max_total_bytes=max_total_bytes,
    )
    if not res.ok:
        msg = res.errors[0] if res.errors else "verification failed"
        raise CliCommandError(
            msg,
            error_type="VerificationError",
            details={
                "pack": str(pack_path),
                "errors": list(res.errors),
                "limits": limits,
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
            "limits": limits,
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
    if args.roots_only and not args.json:
        raise _value_error(
            "--roots-only requires --json",
            details={"flags": {"json": False, "roots_only": True}},
        )
    limits, (max_manifest_bytes, max_artifact_bytes, max_total_bytes) = _validated_verification_limits(args)
    pack_path = _resolve_supported_pack_path(args.pack)
    summary = inspect_pack(
        pack_path,
        max_manifest_bytes=max_manifest_bytes,
        max_artifact_bytes=max_artifact_bytes,
        max_total_bytes=max_total_bytes,
        artifact_preview_limit=int(args.artifact_preview),
    )

    if args.json:
        if args.roots_only:
            print(
                _json_success(
                    "inspect",
                    {
                        "inspected_path": summary["inspected_path"],
                        "pack_type": summary["pack_type"],
                        "pack_root_sha256": summary["pack_root_sha256"],
                        "payload_root_sha256": summary["payload_root_sha256"],
                        "verification_ok": summary["verification_ok"],
                        "verification_errors": summary["verification_errors"],
                    },
                )
            )
            return 0
        summary["limits"] = limits
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


def _consume_bin(args: argparse.Namespace) -> int:
    source_bin = Path(args.source_bin).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    _ensure_repo_clone_consume_bin_defaults(source_bin, out_dir)
    try:
        res = consume_from_source_bin(
            source_bin=source_bin,
            out_dir=out_dir,
            count=int(args.count),
            min_remaining=int(args.min_remaining),
            allow_low_bin=bool(args.allow_low_bin),
            recursive=bool(args.recursive),
            include_hidden=bool(args.include_hidden),
            zip_pack=bool(args.zip),
            derive_seed=bool(args.derive_seed),
            evidence_bundle=bool(args.evidence_bundle),
        )
    except ValueError as exc:
        msg = str(exc).strip() or "consume-bin failed"
        details: Dict[str, object] = {
            "source_bin": str(source_bin),
            "out": str(out_dir),
            "count": int(args.count),
            "min_remaining": int(args.min_remaining),
            "allow_low_bin": bool(args.allow_low_bin),
        }
        if "must not overlap" in msg:
            details["reason"] = "paths overlap"
        elif "low-watermark" in msg:
            details["reason"] = "low-watermark"
        elif "must be a directory" in msg:
            details["reason"] = "missing source bin directory"
        elif "need at least" in msg:
            details["reason"] = "insufficient source files"
        elif "must be > 0" in msg or "must be >= 0" in msg:
            details["reason"] = "invalid numeric argument"
        raise _value_error(msg, details=details) from exc
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
            "pack_dir": str(res.assembled.pack_dir),
            "pack_root_sha256": res.assembled.pack_root_sha256,
            "payload_root_sha256": res.assembled.payload_root_sha256,
            "receipt": res.assembled.receipt,
        }
        if res.assembled.zip_path is not None:
            payload["zip_path"] = str(res.assembled.zip_path)
        if res.assembled.evidence_bundle_path is not None:
            payload["evidence_bundle_path"] = str(res.assembled.evidence_bundle_path)
        if res.assembled.evidence_bundle_sha256:
            payload["evidence_bundle_sha256"] = res.assembled.evidence_bundle_sha256
        print(_json_success(str(getattr(args, "cmd", "consume-bin")), payload))
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
    print(f"pack_dir: {res.assembled.pack_dir}")
    print(f"pack_root_sha256: {res.assembled.pack_root_sha256}")
    print(f"payload_root_sha256: {res.assembled.payload_root_sha256}")
    if res.assembled.zip_path is not None:
        print(f"zip_path: {res.assembled.zip_path}")
    fp = res.assembled.receipt.get("derived_seed_fingerprint_sha256")
    if isinstance(fp, str) and fp:
        print(f"derived_seed_fingerprint_sha256: {fp}")
    if res.assembled.evidence_bundle_path is not None:
        print(f"evidence_bundle_path: {res.assembled.evidence_bundle_path}")
    if res.assembled.evidence_bundle_sha256:
        print(f"evidence_bundle_sha256: {res.assembled.evidence_bundle_sha256}")
    return 0


def build_parser(*, prog: str = "authored-pack") -> argparse.ArgumentParser:
    p = EPSArgumentParser(
        prog=prog,
        description=CLI_DESCRIPTION,
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="{assemble,verify,inspect,consume-bin}")

    assemble = sub.add_parser("assemble", aliases=["stamp"], help="Start here: assemble a folder into a deterministic pack")
    assemble.add_argument("--input", required=True, help="Directory of artifacts to include")
    assemble.add_argument("--out", required=True, help="Content-addressed output directory for the assembled pack")
    assemble.add_argument("--pack-id", default=None, help="Optional human label stored in manifest metadata (affects root)")
    assemble.add_argument("--notes", default=None, help="Optional notes stored in manifest metadata (affects root)")
    assemble.add_argument(
        "--created-at-utc",
        default=None,
        help="Optional ISO8601 UTC timestamp stored in manifest (affects root); omitted when unset",
    )
    assemble.add_argument(
        "--dice",
        action="append",
        default=[],
        help="Optional die roll like d6=4; repeatable; stored in manifest (affects root)",
    )
    assemble.add_argument("--include-hidden", action="store_true", help="Include dotfiles in input scan")
    assemble.add_argument("--zip", action="store_true", help="Write authored_pack.zip alongside pack dir")

    assemble.add_argument("--derive-seed", action="store_true", help=f"Derive reproducible seed material via HKDF ({DEFAULT_DERIVATION_VERSION})")
    assemble.add_argument("--write-seed", action="store_true", help="Write derived seed material as seed_master.{hex,b64} (requires --derive-seed)")
    assemble.add_argument("--print-seed", action="store_true", help="Print derived seed material as seed_master.{hex,b64} to stdout (requires --derive-seed, incompatible with --json)")
    assemble.add_argument("--evidence-bundle", action="store_true", help="Write authored_evidence_<root>.zip + .sha256 (tamper-evident local audit bundle)")

    assemble.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    assemble.set_defaults(func=_assemble)

    verify = sub.add_parser(
        "verify",
        help="Strict check: verify a pack directory or zip",
        description="Strict check: verify a pack directory or zip. Verification limits are operator policy; assemble remains unconstrained.",
    )
    verify.add_argument("--pack", required=True, help="Path to pack dir or authored_pack.zip")
    verify.add_argument(
        "--max-manifest-mib",
        type=int,
        default=DEFAULT_MAX_MANIFEST_MIB,
        help=f"Maximum manifest.json size to accept while verifying (default: {DEFAULT_MAX_MANIFEST_MIB})",
    )
    verify.add_argument(
        "--max-artifact-mib",
        type=int,
        default=DEFAULT_MAX_ARTIFACT_MIB,
        help=f"Maximum single artifact size to accept while verifying (default: {DEFAULT_MAX_ARTIFACT_MIB})",
    )
    verify.add_argument(
        "--max-total-mib",
        type=int,
        default=DEFAULT_MAX_TOTAL_MIB,
        help=f"Maximum total artifact bytes to accept while verifying (default: {DEFAULT_MAX_TOTAL_MIB})",
    )
    verify.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    verify.set_defaults(func=_verify)

    inspect = sub.add_parser(
        "inspect",
        help="Preview: show pack contents and summary",
        description="Preview: inspect a pack with the same operator verification limits used by verify. Assemble remains unconstrained.",
    )
    inspect.add_argument("--pack", required=True, help="Path to pack dir or authored_pack.zip")
    inspect.add_argument(
        "--max-manifest-mib",
        type=int,
        default=DEFAULT_MAX_MANIFEST_MIB,
        help=f"Maximum manifest.json size to accept while inspecting (default: {DEFAULT_MAX_MANIFEST_MIB})",
    )
    inspect.add_argument(
        "--max-artifact-mib",
        type=int,
        default=DEFAULT_MAX_ARTIFACT_MIB,
        help=f"Maximum single artifact size to accept while inspecting (default: {DEFAULT_MAX_ARTIFACT_MIB})",
    )
    inspect.add_argument(
        "--max-total-mib",
        type=int,
        default=DEFAULT_MAX_TOTAL_MIB,
        help=f"Maximum total artifact bytes to accept while inspecting (default: {DEFAULT_MAX_TOTAL_MIB})",
    )
    inspect.add_argument("--artifact-preview", type=int, default=20, help="How many artifact entries to include in the summary preview (default: 20)")
    inspect.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    inspect.add_argument(
        "--roots-only",
        action="store_true",
        help="With --json, emit only pack roots and verification status for machine consumers.",
    )
    inspect.set_defaults(func=_inspect)

    consume_bin = sub.add_parser("consume-bin", aliases=["stamp-bin"], help="Advanced: move files from a source bin into a new pack")
    consume_bin.add_argument(
        "--source-bin",
        default=str(DEFAULT_SOURCE_BIN),
        help="Directory containing source files to consume (moved, not copied) (default in repo clone: ./bins/source_bin; otherwise pass explicit path)",
    )
    consume_bin.add_argument(
        "--out",
        default=str(DEFAULT_AUTHORED_OUT),
        help="Output directory for assembled pack (content-addressed) (default in repo clone: ./bins/authored_out; otherwise pass explicit path)",
    )
    consume_bin.add_argument("--count", type=int, default=7, help="How many files to consume and assemble (default: 7)")
    consume_bin.add_argument("--min-remaining", type=int, default=50, help="Refuse if remaining after consumption would be below this (default: 50)")
    consume_bin.add_argument("--allow-low-bin", action="store_true", help="Proceed even if low-watermark would be violated (prints warning)")
    consume_bin.add_argument("--recursive", action="store_true", help="Scan source bin recursively (default)")
    consume_bin.add_argument("--no-recursive", dest="recursive", action="store_false", help="Only scan top-level of source bin")
    consume_bin.set_defaults(recursive=True)
    consume_bin.add_argument("--include-hidden", action="store_true", help="Include dotfiles while scanning source bin")
    # Push-button defaults: on.
    consume_bin.add_argument("--zip", action=argparse.BooleanOptionalAction, default=True, help="Write authored_pack.zip alongside pack dir (default: on)")
    consume_bin.add_argument(
        "--derive-seed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=f"Derive reproducible seed material via HKDF ({DEFAULT_DERIVATION_VERSION}) (default: on)",
    )
    consume_bin.add_argument(
        "--evidence-bundle",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write authored_evidence_<root>.zip + .sha256 (tamper-evident) (default: on)",
    )
    consume_bin.add_argument("--json", action="store_true", help="Emit JSON envelope to stdout")
    consume_bin.set_defaults(func=_consume_bin)

    _hide_subcommand_aliases_in_help(sub)

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
        return 2
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
            if isinstance(exc, CliCommandError):
                return int(exc.exit_code)
            return 2 if isinstance(exc, ValueError) else 1
        print(f"{prog_name}: error: {msg}", file=sys.stderr)
        if isinstance(exc, ValueError) and "must be a directory" in msg and "--input" in msg:
            print("hint: pass an existing directory path; many terminals let you drag a folder into the window to paste its absolute path.", file=sys.stderr)
        if isinstance(exc, CliCommandError):
            return int(exc.exit_code)
        return 2 if isinstance(exc, ValueError) else 1


if __name__ == "__main__":
    raise SystemExit(main())
