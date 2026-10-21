"""Materialize portable fixture recipes, never infer HTTP expectations from app code."""

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from scripts.fixture_manifest import DEFAULT_ROOT, bundle_path, read_json

FREE_TEXT_FIELDS = {
    "title",
    "description",
    "location",
    "observedTimeText",
    "sourceName",
    "answerText",
}


def utf16_units(value):
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def string_units(value):
    if isinstance(value, str):
        return utf16_units(value)
    if isinstance(value, dict):
        return sum(string_units(child) for child in value.values())
    if isinstance(value, list):
        return sum(string_units(child) for child in value)
    return 0


def location(document, pointer):
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]]
    if not pointer.startswith("/") or not parts:
        raise ValueError("Expected a non-root JSON pointer")
    parent = document
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    return parent, int(parts[-1]) if isinstance(parent, list) else parts[-1]


def materialize(root, recipes, name, ancestors=()):
    if name in ancestors:
        raise ValueError("Cyclic fixture recipe")
    recipe = recipes[name]
    if "baseRecipe" in recipe:
        data = json.loads(materialize(root, recipes, recipe["baseRecipe"], (*ancestors, name)))
    else:
        data = deepcopy(read_json(bundle_path(root, recipe["baseFile"])))
    for operation in recipe.get("operations", []):
        kind = operation["op"]
        if kind == "fillStringTotal":
            remaining = operation["units"] - string_units(data)
            if remaining < 0:
                raise ValueError("String total is already larger than requested")
            for pointer in operation["paths"]:
                parent, key = location(data, pointer)
                count = min(remaining, operation["fieldMaxUnits"] - utf16_units(parent[key]))
                if count < 0:
                    raise ValueError("Field already exceeds fill budget")
                parent[key] += "a" * count
                remaining -= count
            if remaining:
                raise ValueError("Insufficient string fill capacity")
        else:
            parent, key = location(data, operation["path"])
            if kind == "set":
                parent[key] = deepcopy(operation["value"])
            elif kind == "repeatText":
                parent[key] = operation["text"] * operation["count"]
            elif kind == "appendText":
                parent[key] += operation["text"] * operation["count"]
            else:
                raise ValueError(f"Unsupported recipe operation: {kind}")
    encoding = recipe.get("encoding", {})
    raw = json.dumps(
        data,
        ensure_ascii=encoding.get("ensureAscii", True),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    target = encoding.get("padToBytes", len(raw))
    if target < len(raw):
        raise ValueError("Byte target is smaller than serialized JSON")
    return raw + b" " * (target - len(raw))


def maximum(values):
    return max(values, default=0)


def measurements(raw, side):
    data = json.loads(raw)
    result = {f"{side}.bytes": len(raw), f"{side}.stringUnits": string_units(data)}
    if side == "request":
        candidates = data["candidates"]
        basis = [b for candidate in candidates for b in candidate["basis"]]
        result |= {
            "request.content": utf16_units(data["content"]),
            "request.candidates": len(candidates),
            "request.basisPerCandidate": maximum(len(c["basis"]) for c in candidates),
            "request.basisTotal": len(basis),
            "request.basisText": maximum(utf16_units(b["text"]) for b in basis),
            "request.currentText": maximum(
                utf16_units(value)
                for c in candidates
                for key, value in c["currentValues"].items()
                if key in FREE_TEXT_FIELDS and isinstance(value, str)
            ),
        }
    else:
        # Error envelopes share the wire/string budget but contain no suggestions.
        suggestions = data.get("suggestions", [])
        refs = [ref for s in suggestions for ref in s["basisReferences"]]
        result |= {
            "response.suggestions": len(suggestions),
            "response.title": maximum(utf16_units(s["title"]) for s in suggestions),
            "response.evidence": maximum(utf16_units(s["evidence"]) for s in suggestions),
            "response.changeText": maximum(
                utf16_units(value)
                for s in suggestions
                for key, value in s["changes"].items()
                if key in FREE_TEXT_FIELDS
            ),
            "response.candidateItemIds": maximum(len(s["candidateItemIds"]) for s in suggestions),
            "response.basisReferences": maximum(len(s["basisReferences"]) for s in suggestions),
            "response.basisIdsPerItem": maximum(len(ref["basisIds"]) for ref in refs),
            "response.basisIdsPerSuggestion": maximum(
                sum(len(ref["basisIds"]) for ref in s["basisReferences"]) for s in suggestions
            ),
        }
    return {
        "byteLength": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "stringUnits": string_units(data),
        "measurements": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--write-metadata",
        action="store_true",
        help="Refresh generated bytes/lengths only, never case expectations",
    )
    parser.add_argument("--export", type=Path, help="Export payloads to a new external directory")
    args = parser.parse_args()
    path = args.root / "generated.json"
    generated = read_json(path)
    if args.export:
        if args.export.resolve().is_relative_to(args.root.resolve()):
            parser.error("Export outside the shared bundle")
        args.export.mkdir(parents=True, exist_ok=False)
    for name, entry in generated["payloads"].items():
        if not name.replace("-", "").isalnum():
            raise ValueError("Unsafe generated payload name")
        raw = materialize(args.root, generated["recipes"], name)
        observed = measurements(raw, entry["side"])
        if args.write_metadata:
            entry["expected"] = observed
        elif entry["expected"] != observed:
            raise SystemExit(f"Generated payload mismatch: {name}")
        if args.export:
            (args.export / f"{name}.json").write_bytes(raw)
    if args.write_metadata:
        path.write_text(
            json.dumps(generated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(f"{generated['revision']}: {len(generated['payloads'])} generated payloads verified")


if __name__ == "__main__":
    main()
