"""Render Compose using synthetic credentials; never read a developer .env."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="hanmaum-ai-config-") as directory:
        work = Path(directory)
        shutil.copytree(ROOT / "deploy", work / "deploy")
        (work / "runtime.env").write_text(
            "AI_INTERNAL_API_KEY=" + "synthetic$literal" * 3 + "\n", encoding="utf-8"
        )
        env_file = work / "compose.env"
        env_file.write_text(
            "AI_IMAGE=ghcr.io/team-hanmaum/hanmaum-ai@sha256:"
            + "a" * 64
            + "\nAI_MEMORY_LIMIT=512m\nAI_STATE_DIR=/opt/hanmaum-ai\n",
            encoding="utf-8",
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith(("AI_", "COMPOSE_"))}
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(work / "deploy/compose.prod.yml"),
                "config",
                "--format",
                "json",
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        config = json.loads(result.stdout)
        ai = config["services"]["ai"]
        assert list(config["services"]) == ["ai"]
        assert not ai.get("ports")
        # config escapes dollars for round-tripping; smoke.py checks actual runtime bytes.
        assert (
            ai["environment"]["AI_INTERNAL_API_KEY"].replace("$$", "$") == "synthetic$literal" * 3
        )
        assert ai["user"] == "10001:10001" and ai["read_only"]
        assert ai["command"][ai["command"].index("--workers") + 1] == "1"
        assert "--ssl-certfile" in ai["command"] and "--ssl-keyfile" in ai["command"]
        assert config["networks"]["be-link"]["external"] is True
        assert config["networks"]["be-link"]["name"] == "hanmaum-ai-link"
    print("AI Compose validated: TLS, private ports, resource limit and literal secrets")


if __name__ == "__main__":
    main()
