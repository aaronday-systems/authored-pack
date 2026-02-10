from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .manifest import DEFAULT_DERIVATION_VERSION, stable_dumps
from .pack import StampResult, stamp_pack, verify_pack


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

    res: StampResult = stamp_pack(
        input_dir=Path(args.input),
        out_dir=Path(args.out),
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
        print(stable_dumps(res.receipt))
    else:
        print(f"pack_dir: {res.pack_dir}")
        print(f"entropy_root_sha256: {res.root_sha256}")
        if args.derive_seed:
            fp = res.receipt.get("seed_fingerprint_sha256")
            if isinstance(fp, str) and fp:
                print(f"seed_fingerprint_sha256: {fp}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    res = verify_pack(Path(args.pack))
    if args.json:
        payload = {
            "ok": res.ok,
            "entropy_root_sha256": res.root_sha256,
            "artifact_count_verified": res.file_count,
            "artifact_bytes_verified": res.total_bytes,
            "errors": list(res.errors),
        }
        print(stable_dumps(payload))
    else:
        if res.ok:
            print("ok")
            print(f"entropy_root_sha256: {res.root_sha256}")
            print(f"artifact_count_verified: {res.file_count}")
            print(f"artifact_bytes_verified: {res.total_bytes}")
        else:
            print("verify_failed", file=sys.stderr)
            for e in res.errors:
                print(f"- {e}", file=sys.stderr)
    return 0 if res.ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eps", description="Entropy Pack Stamper (EPS)")
    sub = p.add_subparsers(dest="cmd", required=True)

    stamp = sub.add_parser("stamp", help="Stamp an EntropyPack from an input directory")
    stamp.add_argument("--input", required=True, help="Directory of artifacts to include")
    stamp.add_argument("--out", required=True, help="Output directory for stamped pack (content-addressed)")
    stamp.add_argument("--pack-id", default=None, help="Optional human label stored in manifest (affects root)")
    stamp.add_argument("--notes", default=None, help="Optional notes stored in manifest (affects root)")
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

    stamp.add_argument("--derive-seed", action="store_true", help=f"Derive seed_master via HKDF ({DEFAULT_DERIVATION_VERSION})")
    stamp.add_argument("--write-seed", action="store_true", help="Write seed_master.{hex,b64} (chmod 600 best-effort)")
    stamp.add_argument("--print-seed", action="store_true", help="Print seed_master.{hex,b64} to stdout (no files)")
    stamp.add_argument("--evidence-bundle", action="store_true", help="Write eps_evidence_<root>.zip + .sha256 (tamper-evident)")

    stamp.add_argument("--json", action="store_true", help="Emit receipt JSON to stdout")
    stamp.set_defaults(func=_stamp)

    verify = sub.add_parser("verify", help="Verify an EntropyPack directory or .zip")
    verify.add_argument("--pack", required=True, help="Path to pack dir or entropy_pack.zip")
    verify.add_argument("--json", action="store_true", help="Emit verification JSON to stdout")
    verify.set_defaults(func=_verify)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(list(argv) if argv is not None else None)
    if not hasattr(ns, "func"):
        parser.print_help()
        return 2
    try:
        return int(ns.func(ns))
    except ValueError as exc:
        # Keep CLI UX clean: most user-caused validation failures should not
        # show a Python traceback.
        msg = str(exc).strip() or exc.__class__.__name__
        print(f"eps: error: {msg}", file=sys.stderr)
        if "must be a directory" in msg and "--input" in msg:
            print("hint: pass an existing directory path; on macOS you can drag a folder into the terminal to paste its absolute path.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
