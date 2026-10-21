"""CLI failure boundaries: preserve source metadata and publish only complete exports."""

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts import fixture_payloads


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    raw = b'{"code":"AI_OVERLOADED","message":"synthetic"}'
    (root / "base.json").write_bytes(raw)
    data = {
        "revision": "test",
        "recipes": {name: {"baseFile": "base.json"} for name in ("first", "second")},
        "payloads": {
            name: {"side": "response", "expected": fixture_payloads.measurements(raw, "response")}
            for name in ("first", "second")
        },
    }
    path = root / "generated.json"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    return root, data


def invoke(monkeypatch, root, *args):
    monkeypatch.setattr(sys, "argv", ["fixture_payloads", "--root", str(root), *map(str, args)])
    fixture_payloads.main()


def assert_no_staging(root, output):
    assert not list(root.glob(".generated.json.*.tmp"))
    assert not list(output.parent.glob(f".{output.name}.*"))


@pytest.mark.parametrize("side", ["respnose", "", "REQUEST", None, True, []])
def test_unknown_side_is_rejected_before_decoding(side):
    with pytest.raises(ValueError, match="Unsupported payload side"):
        fixture_payloads.measurements(b"not even JSON", side)


@pytest.mark.parametrize("refresh", [False, True])
def test_invalid_side_never_publishes_export_or_refreshes_metadata(
    bundle, tmp_path, monkeypatch, refresh
):
    root, data = bundle
    data["payloads"]["second"]["side"] = "respnose"
    path = root / "generated.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    original = path.read_bytes()
    output = tmp_path / "export"
    args = ["--export", output, *(["--write-metadata"] if refresh else [])]
    with pytest.raises(ValueError, match="Unsupported payload side"):
        invoke(monkeypatch, root, *args)
    assert path.read_bytes() == original
    assert not output.exists()
    assert_no_staging(root, output)


@pytest.mark.parametrize("failure", ["generation", "mismatch", "write", "interrupt"])
def test_incomplete_export_is_not_published_and_same_path_can_be_retried(
    bundle, tmp_path, monkeypatch, failure
):
    root, data = bundle
    output = tmp_path / "export"
    path = root / "generated.json"
    original = path.read_bytes()
    materialize = fixture_payloads.materialize
    write_bytes = Path.write_bytes

    def broken_materialize(root, recipes, name, ancestors=()):
        if name == "second":
            raise ValueError("generation failed")
        return materialize(root, recipes, name, ancestors)

    def broken_write(path, raw):
        assert not output.exists()
        if path.name == "second.json":
            write_bytes(path, raw[:5])
            if failure == "interrupt":
                raise KeyboardInterrupt()
            raise OSError("disk write failed")
        return write_bytes(path, raw)

    error = {
        "generation": ValueError,
        "mismatch": SystemExit,
        "write": OSError,
        "interrupt": KeyboardInterrupt,
    }[failure]
    with monkeypatch.context() as patch:
        if failure == "generation":
            patch.setattr(fixture_payloads, "materialize", broken_materialize)
        elif failure == "mismatch":
            data["payloads"]["second"]["expected"]["byteLength"] += 1
            path.write_text(json.dumps(data), encoding="utf-8")
        else:
            patch.setattr(Path, "write_bytes", broken_write)
        before = path.read_bytes()
        with pytest.raises(error):
            invoke(patch, root, "--export", output)
        assert path.read_bytes() == before
    assert not output.exists()
    assert_no_staging(root, output)

    path.write_bytes(original)
    invoke(monkeypatch, root, "--export", output)
    assert {p.name for p in output.iterdir()} == {"first.json", "second.json"}
    for exported in output.iterdir():
        assert exported.read_bytes() == (root / "base.json").read_bytes()
    assert_no_staging(root, output)


@pytest.mark.parametrize("phase", ["write", "flush", "replace"])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_metadata_failure_keeps_original_and_cleans_temporary_outputs(
    bundle, tmp_path, monkeypatch, phase, error
):
    root, data = bundle
    path = root / "generated.json"
    data["payloads"]["second"]["expected"] = {}
    path.write_text(json.dumps(data), encoding="utf-8")
    original = path.read_bytes()
    output = tmp_path / "export"
    named_temporary_file = fixture_payloads.NamedTemporaryFile

    def fail(*args):
        assert path.read_bytes() == original
        assert not output.exists()
        if phase == "replace":
            # The replacement is complete and closed before publication is attempted.
            temporary, destination = args
            assert destination == path and temporary.parent == root
            assert json.loads(temporary.read_bytes())["payloads"]["second"]["expected"]
        raise error("interrupted metadata update")

    @contextmanager
    def broken_temporary_file(*args, **kwargs):
        with named_temporary_file(*args, **kwargs) as stream:
            write = stream.write

            def partial_write(raw):
                write(raw[:5])
                fail()

            stream.write = partial_write
            yield stream

    with monkeypatch.context() as patch:
        if phase == "write":
            patch.setattr(fixture_payloads, "NamedTemporaryFile", broken_temporary_file)
        elif phase == "flush":
            patch.setattr(fixture_payloads.os, "fsync", fail)
        else:
            patch.setattr(fixture_payloads.os, "replace", fail)
        with pytest.raises(error):
            invoke(patch, root, "--write-metadata", "--export", output)
    assert path.read_bytes() == original
    assert not output.exists()
    assert_no_staging(root, output)

    invoke(monkeypatch, root, "--write-metadata", "--export", output)
    updated = json.loads(path.read_bytes())
    expected = fixture_payloads.measurements((root / "base.json").read_bytes(), "response")
    assert updated["payloads"]["second"]["expected"] == expected
    assert {p.name for p in output.iterdir()} == {"first.json", "second.json"}
    assert_no_staging(root, output)


def test_export_publication_failure_leaves_no_destination_and_allows_retry(
    bundle, tmp_path, monkeypatch
):
    root, _ = bundle
    output = tmp_path / "export"
    original = (root / "generated.json").read_bytes()

    def fail_publish(staging, destination):
        assert destination == output and not destination.exists()
        assert {p.name for p in staging.iterdir()} == {"first.json", "second.json"}
        raise OSError("rename failed")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", fail_publish)
        with pytest.raises(OSError, match="rename failed"):
            invoke(patch, root, "--export", output)
    assert not output.exists()
    assert (root / "generated.json").read_bytes() == original
    assert_no_staging(root, output)
    invoke(monkeypatch, root, "--export", output)
    assert len(list(output.iterdir())) == 2


@pytest.mark.parametrize("inside_bundle", [False, True])
def test_export_never_overwrites_existing_paths_or_writes_inside_bundle(
    bundle, tmp_path, monkeypatch, inside_bundle
):
    root, _ = bundle
    output = (root if inside_bundle else tmp_path) / "export"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit if inside_bundle else FileExistsError):
        invoke(monkeypatch, root, "--export", output)
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(output.iterdir()) == [sentinel]
    assert_no_staging(root, output)
