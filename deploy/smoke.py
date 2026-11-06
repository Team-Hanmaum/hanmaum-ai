"""Isolated Docker test: synthetic secrets, unique resources, no provider network access."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from deploy.release import runtime_values

SOURCE = Path(__file__).resolve().parents[1]


def run(*command, timeout=180):
    result = subprocess.run(
        [str(part) for part in command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Smoke command failed: {result.stderr[-2000:]}")
    return result.stdout


def main(image):
    name = "hanmaum-ai-test-" + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="hanmaum-ai-smoke-") as directory:
        work = Path(directory)
        shutil.copytree(SOURCE / "deploy", work / "deploy")
        state = work / "state"
        state.mkdir()
        values = runtime_values(
            {
                "AI_INTERNAL_API_KEY": "synthetic$literal" * 3,
                "OPENAI_API_KEY": "synthetic-provider-key",
                "AI_V2_HMAC_KEYS": json.dumps({"test-1": "synthetic-receipt-key" * 3}),
                "AI_V2_ACTIVE_HMAC_KEY_VERSION": "test-1",
            }
        )
        (work / "runtime.env").write_text(
            "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8"
        )
        (work / "compose.env").write_text(
            f"AI_IMAGE={image}\nAI_MEMORY_LIMIT=512m\nAI_STATE_DIR={state.as_posix()}\n",
            encoding="utf-8",
        )
        # Both networks are internal. Even an accidental provider request cannot leave.
        (work / "override.json").write_text(
            json.dumps(
                {
                    "services": {
                        "ai": {
                            "volumes": [
                                f"{(work / 'deploy/smoke_probe.py').as_posix()}:/probe.py:ro"
                            ]
                        }
                    },
                    "networks": {"be-link": {"name": name}, "egress": {"internal": True}},
                }
            ),
            encoding="utf-8",
        )
        command = [
            "docker",
            "compose",
            "--env-file",
            str(work / "compose.env"),
            "-p",
            name,
            "-f",
            str(work / "deploy/compose.prod.yml"),
            "-f",
            str(work / "override.json"),
        ]
        created = False
        try:
            run("docker", "network", "create", "--internal", name)
            created = True
            # Exercise the exact production TLS generator inside Linux, even on Windows.
            run(
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--mount",
                f"type=bind,src={work},dst=/fixtures",
                "--workdir",
                "/fixtures",
                image,
                "/app/.venv/bin/python",
                "-c",
                "import os; from pathlib import Path; "
                "from deploy.host import provision_tls,verify_tls; os.umask(0o077); "
                "root=Path('/fixtures/state'); provision_tls(root); verify_tls(root); "
                "data=root/'data'; data.mkdir(mode=0o700); os.chown(data,10001,10001)",
            )
            run(
                *command,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "ai",
                "/app/.venv/bin/python",
                "-m",
                "app.v2.initialize_receipts",
                "/var/lib/hanmaum-ai/receipts.sqlite3",
            )
            run(
                *command,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "ai",
                "/app/.venv/bin/python",
                "/probe.py",
                "seed",
            )
            run(*command, "up", "-d", "--wait", "--wait-timeout", "120")
            run(*command, "exec", "-T", "ai", "/app/.venv/bin/python", "/probe.py")
            run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120")
            run(*command, "exec", "-T", "ai", "/app/.venv/bin/python", "/probe.py")
            print(run(*command, "stats", "--no-stream", "--format", "json").strip())
            print("AI production container smoke passed; no model calls or public ports")
        except Exception:
            # These containers contain only synthetic values; keep actionable CI diagnostics.
            print(run(*command, "logs", "--tail", "30"))
            diagnostic = subprocess.run(
                [
                    *command,
                    "exec",
                    "-T",
                    "ai",
                    "/app/.venv/bin/python",
                    "-c",
                    "import runpy; runpy.run_path('/opt/hanmaum-ai/healthcheck.py')['main']()",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            print(diagnostic.stderr[-2000:])
            raise
        finally:
            if created:
                run(*command, "down", "--timeout", "15")
                run("docker", "network", "rm", name)
            uid = os.getuid() if hasattr(os, "getuid") else 0
            gid = os.getgid() if hasattr(os, "getgid") else 0
            run(
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--mount",
                f"type=bind,src={state},dst=/state",
                image,
                "/app/.venv/bin/python",
                "-c",
                "import os; from pathlib import Path; "
                "paths=[Path('/state'),*Path('/state').rglob('*')]; "
                f"[(p.chmod(0o777),os.chown(p,{uid},{gid})) for p in paths]",
            )


if __name__ == "__main__":
    main(sys.argv[1])
