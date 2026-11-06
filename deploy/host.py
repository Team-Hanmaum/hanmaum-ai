"""Root-only host deployment. Called under the shared BE/AI flock by activate.sh."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from deploy.release import (
    RELEASE_ID,
    compare_stable,
    read_runtime,
    require_latest,
    validate_metadata,
    write_private,
)

ROOT = Path("/opt/hanmaum-ai")
NETWORK = "hanmaum-ai-link"


def run(*arguments, input_text=None, timeout=180):
    result = subprocess.run(
        [str(value) for value in arguments],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        # Some Docker errors embed configuration; do not relay stdout/stderr.
        raise RuntimeError(f"{arguments[0]} operation failed; inspect the server privately")
    return result.stdout.strip()


def atomic_json(path, value):
    temporary = path.with_name(path.name + ".next")
    temporary.unlink(missing_ok=True)
    write_private(temporary, json.dumps(value) + "\n")
    temporary.replace(path)


def current_release(root):
    path = root / "current"
    if not path.is_symlink():
        if path.exists():
            raise ValueError("current must be a managed symlink")
        return None
    target = path.resolve(strict=True)
    if target.parent != root / "releases" or not re.fullmatch(RELEASE_ID, target.name):
        raise ValueError("Invalid current release")
    return target


def compose(release, *arguments):
    return run(
        "docker",
        "compose",
        "--env-file",
        release / "compose.env",
        "-p",
        "hanmaum-ai-prod",
        "-f",
        release / "deploy/compose.prod.yml",
        *arguments,
    )


def verify_tls(root):
    tls = root / "tls"
    run(
        "openssl",
        "verify",
        "-CAfile",
        tls / "ca.crt",
        "-verify_hostname",
        "hanmaum-ai",
        tls / "server.crt",
    )
    run("openssl", "x509", "-in", tls / "server.crt", "-checkend", "604800", "-noout")
    cert_key = run("openssl", "x509", "-in", tls / "server.crt", "-pubkey", "-noout")
    private_key = run("openssl", "pkey", "-in", tls / "server.key", "-pubout")
    if cert_key != private_key:
        raise ValueError("TLS key/certificate mismatch")


def provision_tls(root):
    """Create a CA only on explicit first setup. Partial state is never overwritten."""
    if (root / "tls").exists() or (root / "ca").exists():
        verify_tls(root)
        return
    with tempfile.TemporaryDirectory(prefix="tls-", dir=root) as directory:
        work = Path(directory)
        ca, tls = work / "ca", work / "tls"
        ca.mkdir(mode=0o700)
        tls.mkdir(mode=0o750)
        run(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-days",
            "3650",
            "-subj",
            "/CN=Hanmaum AI Internal CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            ca / "ca.key",
            "-out",
            ca / "ca.crt",
        )
        issue_leaf(ca, tls)
        ca.rename(root / "ca")
        tls.rename(root / "tls")


def issue_leaf(ca, tls):
    run(
        "openssl",
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=hanmaum-ai",
        "-keyout",
        tls / "server.key",
        "-out",
        tls / "server.csr",
    )
    extensions = tls / "extensions.cnf"
    extensions.write_text(
        "subjectAltName=DNS:hanmaum-ai\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n",
        encoding="ascii",
    )
    run(
        "openssl",
        "x509",
        "-req",
        "-in",
        tls / "server.csr",
        "-CA",
        ca / "ca.crt",
        "-CAkey",
        ca / "ca.key",
        "-CAcreateserial",
        "-days",
        "365",
        "-sha256",
        "-extfile",
        extensions,
        "-out",
        tls / "server.crt",
    )
    shutil.copyfile(ca / "ca.crt", tls / "ca.crt")
    extensions.unlink()
    (tls / "server.csr").unlink()
    os.chown(tls, 10001, 10001)
    tls.chmod(0o750)
    for path in tls.iterdir():
        os.chown(path, 10001, 10001)
        path.chmod(0o400 if path.suffix == ".key" else 0o444)


def ensure_network(initialize):
    existing = run(
        "docker", "network", "ls", "--filter", f"name=^{NETWORK}$", "--format", "{{.Name}}"
    )
    if not existing:
        if not initialize:
            raise ValueError("Shared AI network missing; investigate instead of recreating it")
        run("docker", "network", "create", "--driver", "bridge", "--internal", NETWORK)
    info = json.loads(run("docker", "network", "inspect", NETWORK))[0]
    if info["Driver"] != "bridge" or not info["Internal"]:
        raise ValueError("AI link must be an internal bridge")


def check_be_key(candidate, baseline=Path("/opt/hanmaum/credential-baseline.env")):
    # Read only the generated BE key; never execute/source a shell environment file.
    for line in baseline.read_text(encoding="utf-8").splitlines():
        if line.startswith("AI_INTERNAL_API_KEY="):
            value = json.loads(line.partition("=")[2]).replace("$$", "$")
            if value != candidate["AI_INTERNAL_API_KEY"]:
                raise ValueError("AI internal key must match the existing BE key")
            return
    raise ValueError("Existing BE internal key not found")


def prepare_storage(root, image, initialize):
    marker = root / "initialized"
    data = root / "data"
    database = data / "receipts.sqlite3"
    if marker.exists():
        if not database.is_file():
            raise ValueError("Receipt store lost; restore safely before restarting")
        return
    if not initialize:
        raise ValueError("First deployment requires explicit initialize_ai")
    data.mkdir(mode=0o700, exist_ok=True)
    os.chown(data, 10001, 10001)
    # Initialization is idempotent only while initial provisioning is incomplete.
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "10001:10001",
        "--mount",
        f"type=bind,src={data},dst=/data",
        image,
        "/app/.venv/bin/python",
        "-m",
        "app.v2.initialize_receipts",
        "/data/receipts.sqlite3",
    )
    write_private(marker, "Receipt store provisioned. Never delete to repair a lost database.\n")


def backup_receipts(root, image, release_id):
    backups = root / "backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    os.chown(backups, 10001, 10001)
    code = (
        "import sqlite3; from pathlib import Path; from uuid import UUID; "
        "from app.v2.receipts import SQLiteReceiptStore; "
        "SQLiteReceiptStore(Path('/data/receipts.sqlite3'),lock_timeout_seconds=.25)"
        ".get('v2',UUID(int=0)); "
        "source=sqlite3.connect('file:/data/receipts.sqlite3?mode=rw',uri=True); "
        f"target=sqlite3.connect('/backups/{release_id}.sqlite3'); "
        "source.backup(target); target.close(); source.close()"
    )
    run(
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "10001:10001",
        "--mount",
        f"type=bind,src={root / 'data'},dst=/data",
        "--mount",
        f"type=bind,src={backups},dst=/backups",
        image,
        "/app/.venv/bin/python",
        "-c",
        code,
    )


def apply_release(root, release, previous):
    state = root / "deployment-state.json"
    atomic_json(
        state, {"phase": "applying", "release": str(release), "previous": str(previous or "")}
    )
    try:
        compose(release, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "ai")
    except Exception:
        try:
            if previous:
                compose(previous, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120", "ai")
            else:
                compose(release, "stop", "ai")
        except Exception:
            atomic_json(state, {"phase": "recovery-required", "release": str(release)})
            raise RuntimeError(
                "AI recovery failed; inspect the server before another deploy"
            ) from None
        atomic_json(
            state, {"phase": "failed", "release": str(release), "restored": str(previous or "")}
        )
        raise RuntimeError(
            "AI deployment failed; previous AI restored or first candidate stopped"
        ) from None
    temporary = root / "current.next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(release)
    temporary.replace(root / "current")
    atomic_json(state, {"phase": "succeeded", "release": str(release)})


def activate(incoming):
    root = ROOT
    info = json.loads((incoming / "release.json").read_text(encoding="utf-8"))
    validate_metadata(info)
    candidate = read_runtime(incoming / "runtime.env")
    check_be_key(candidate)
    token = (incoming / "registry-token").read_text(encoding="utf-8").strip()
    require_latest(info["sha"], token)
    root.mkdir(mode=0o700, exist_ok=True)
    (root / "releases").mkdir(mode=0o700, exist_ok=True)
    state = root / "deployment-state.json"
    if state.exists() and json.loads(state.read_text())["phase"] in (
        "applying",
        "recovery-required",
    ):
        raise ValueError("Interrupted deployment requires operator recovery")
    previous = current_release(root)
    if previous and not (root / "initialized").exists():
        raise ValueError("Initialization marker lost; operator recovery required")
    baseline = root / "credential-baseline.env"
    if baseline.exists():
        compare_stable(read_runtime(baseline), candidate)
    else:
        if previous or (root / "initialized").exists() or not info["initialize"]:
            raise ValueError("Baseline missing; explicit first setup or recovery required")
        write_private(baseline, (incoming / "runtime.env").read_text(encoding="utf-8"))
    release = root / "releases" / info["id"]
    release.mkdir(mode=0o700)
    shutil.copytree(incoming / "deploy", release / "deploy")
    for path in (release / "deploy").rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    for name in ("runtime.env", "release.json"):
        write_private(release / name, (incoming / name).read_text(encoding="utf-8"))
    write_private(
        release / "compose.env",
        f"AI_IMAGE={info['image']}\nAI_MEMORY_LIMIT={info['memory']}\nAI_STATE_DIR={root}\n",
    )
    # A temporary Docker config never leaves a long-lived GHCR token on the server.
    with tempfile.TemporaryDirectory(prefix="registry-", dir=root) as directory:
        old_config = os.environ.get("DOCKER_CONFIG")
        os.environ["DOCKER_CONFIG"] = directory
        try:
            run(
                "docker",
                "login",
                "ghcr.io",
                "-u",
                info["actor"],
                "--password-stdin",
                input_text=token,
            )
            run("docker", "pull", info["image"], timeout=600)
        finally:
            if old_config is None:
                os.environ.pop("DOCKER_CONFIG", None)
            else:
                os.environ["DOCKER_CONFIG"] = old_config
    revision = run(
        "docker",
        "image",
        "inspect",
        info["image"],
        "--format",
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
    )
    if revision != info["sha"]:
        raise ValueError("Image revision does not match the release SHA")
    if info["initialize"] and not (root / "initialized").exists():
        provision_tls(root)
    verify_tls(root)
    ensure_network(info["initialize"] and not (root / "initialized").exists())
    prepare_storage(root, info["image"], info["initialize"])
    compose(release, "config", "--quiet")
    backup_receipts(root, info["image"], info["id"])
    require_latest(info["sha"], token)
    apply_release(root, release, previous)
    for name in ("hanmaum-ai-tls-renew.service", "hanmaum-ai-tls-renew.timer"):
        shutil.copyfile(release / "deploy/systemd" / name, Path("/etc/systemd/system") / name)
        (Path("/etc/systemd/system") / name).chmod(0o644)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", "hanmaum-ai-tls-renew.timer")
    print(f"AI deployed: {info['sha']}; TLS/readiness passed. No model call made.")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        if os.geteuid() != 0 or len(sys.argv) != 2:
            raise ValueError("Root and an upload directory are required")
        incoming = Path(sys.argv[1]).resolve(strict=True)
        if not re.fullmatch(r"/tmp/hanmaum-ai-cd\.[a-zA-Z0-9]{10}", str(incoming)):
            raise ValueError("Invalid upload directory")
        activate(incoming)
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.TimeoutExpired) as error:
        # Validation messages are deliberately value-free; avoid arbitrary parser detail.
        if isinstance(error, (RuntimeError, ValueError)) and not isinstance(
            error, json.JSONDecodeError
        ):
            print(str(error), file=sys.stderr)
        else:
            print("AI deployment failed; inspect private server state", file=sys.stderr)
        sys.exit(1)
