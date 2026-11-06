"""Renew the server leaf only. The BE-trusted CA and receipt keys stay unchanged."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from deploy.host import ROOT, atomic_json, compose, current_release, issue_leaf, run, verify_tls


def renew(root):
    state = root / "deployment-state.json"
    if state.exists() and json.loads(state.read_text())["phase"] in (
        "applying",
        "recovery-required",
    ):
        raise ValueError("Resolve interrupted deployment before TLS maintenance")
    current = current_release(root)
    if current is None:
        raise ValueError("No successful AI release")
    # A CA rotation needs a separate BE trust rollout.
    run("openssl", "x509", "-in", root / "ca/ca.crt", "-checkend", "34560000", "-noout")
    check = subprocess.run(
        ["openssl", "x509", "-in", str(root / "tls/server.crt"), "-checkend", "2592000", "-noout"],
        capture_output=True,
        check=False,
    )
    if check.returncode == 0:
        verify_tls(root)
        return
    with tempfile.TemporaryDirectory(prefix="renew-", dir=root) as directory:
        work = Path(directory)
        (work / "tls").mkdir(mode=0o750)
        issue_leaf(root / "ca", work / "tls")
        verify_tls(work)
        previous = work / "previous"
        try:
            (root / "tls").rename(previous)
            (work / "tls").rename(root / "tls")
            compose(
                current, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "ai"
            )
        except Exception:
            if not previous.exists():
                raise  # The original directory was never moved; leave it untouched.
            if (root / "tls").exists():
                shutil.rmtree(root / "tls")
            previous.rename(root / "tls")
            try:
                compose(
                    current, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120", "ai"
                )
            except Exception:
                atomic_json(
                    state,
                    {"phase": "recovery-required", "release": str(current), "operation": "tls"},
                )
                raise RuntimeError(
                    "TLS renewal recovery failed; operator recovery required"
                ) from None
            raise RuntimeError("TLS renewal failed; previous certificate restored") from None
    print("AI server certificate renewed; CA unchanged")


if __name__ == "__main__":
    import fcntl

    os.umask(0o077)
    if os.geteuid() != 0:
        raise SystemExit("Root required")
    with Path("/run/lock/hanmaum-deploy.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(0) from None  # Retry on the next daily timer.
        renew(ROOT)
