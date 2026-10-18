"""Inventory shared fixture bytes; never infer or update contract expectations."""

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "tests/fixtures/v2"
SUITES = {
    "STATIC": "cases/static.json",
    "RECEIPT": "cases/receipts.json",
    "BACKEND": "cases/backend.json",
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate fixture metadata key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Non-JSON constant: {value}")


def read_json(path):
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


def bundle_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError(f"Invalid bundle path: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or Path(relative).is_absolute():
        raise ValueError(f"Path outside fixture bundle: {relative}")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing fixture file: {relative}")
    return path


def inventory_sort_key(relative_path):
    # Preserve the published Windows-generated inventory on every OS. Native Path
    # ordering differs on Linux (notably README.md versus lowercase directories).
    parts = relative_path.parts
    return tuple(part.casefold() for part in parts), parts


def build_manifest(root, revision):
    files = []
    for path in sorted(
        root.rglob("*"), key=lambda path: inventory_sort_key(path.relative_to(root))
    ):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not portable fixtures: {path}")
        if not path.is_file() or path == root / "manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    case_ids = []
    for category, relative in SUITES.items():
        cases = read_json(bundle_path(root, relative))
        for case in cases:
            if case["category"] != category or case["contractVersion"] != "v2":
                raise ValueError(f"Wrong suite or contract version: {case['caseId']}")
            case_ids.append(case["caseId"])
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Duplicate caseId")
    return {
        "formatVersion": 1,
        "contractVersion": "v2",
        "revision": revision,
        "sourceRepository": "Team-Hanmaum/hanmaum-ai",
        "suites": SUITES,
        "caseIds": sorted(case_ids),
        "files": files,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--write", action="store_true", help="Write reviewed file hashes only")
    parser.add_argument("--revision", help="Required for --write; agree before sharing")
    args = parser.parse_args()
    manifest_path = args.root / "manifest.json"
    if args.write:
        if not args.revision:
            parser.error("--write requires --revision")
        manifest = build_manifest(args.root, args.revision)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        if args.revision:
            parser.error("--revision is only supported with --write")
        manifest = read_json(manifest_path)
        if manifest != build_manifest(args.root, manifest["revision"]):
            raise SystemExit("Fixture inventory mismatch; review changes before updating hashes.")
    print(
        f"{manifest['revision']}: {len(manifest['caseIds'])} cases, {len(manifest['files'])} files"
    )


if __name__ == "__main__":
    main()
