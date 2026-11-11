"""Choose a trusted dev release before granting image or deployment write permissions."""

import json
import os
import re
from pathlib import Path

from deploy.release import REPOSITORY, StaleRelease, require_latest


def select_sha(event_name, event, repository, ref, workflow_sha):
    if repository != REPOSITORY or ref != "refs/heads/dev":
        return None
    if event_name == "workflow_dispatch":
        sha = workflow_sha
    elif event_name == "workflow_run":
        run = event.get("workflow_run") or {}
        if (
            event.get("action") != "completed"
            or run.get("name") != "AI CI"
            or run.get("event") != "push"
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("head_branch") != "dev"
            or (run.get("head_repository") or {}).get("full_name") != REPOSITORY
        ):
            return None
        # GITHUB_SHA identifies the CD workflow's default branch, not the CI commit.
        sha = run.get("head_sha")
    else:
        return None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid release SHA")
    return sha


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    sha = select_sha(
        os.environ["GITHUB_EVENT_NAME"],
        event,
        os.environ["GITHUB_REPOSITORY"],
        os.environ["GITHUB_REF"],
        os.environ["GITHUB_SHA"],
    )
    if sha is None:
        print("::notice::No deployment: expected a successful AI CI dev push or manual dev run.")
        return
    try:
        require_latest(sha, os.environ["GH_TOKEN"])
    except StaleRelease:
        print("::notice::No deployment: this commit is no longer the dev head.")
        return
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"sha={sha}\n")
    print(f"Selected release commit: {sha}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError, AttributeError):
        # Event payloads and credentials are never included in error output.
        raise SystemExit("Release selection failed; no deployment authorized") from None
