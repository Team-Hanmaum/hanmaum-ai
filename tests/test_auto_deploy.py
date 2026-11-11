"""Release admission and SHA propagation, without GitHub writes or a production host."""

import json
import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from deploy import release, select_release

ROOT = Path(__file__).resolve().parents[1]
CI_SHA = "a" * 40
WORKFLOW_SHA = "b" * 40


def completed_ci():
    return {
        "action": "completed",
        "workflow_run": {
            "name": "AI CI",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "dev",
            "head_repository": {"full_name": release.REPOSITORY},
            "head_sha": CI_SHA,
        },
    }


def select(event, event_name="workflow_run", **changes):
    settings = {
        "repository": release.REPOSITORY,
        "ref": "refs/heads/dev",
        "workflow_sha": WORKFLOW_SHA,
    } | changes
    return select_release.select_sha(event_name, event, **settings)


def test_ci_commit_is_selected_even_when_workflow_commit_differs():
    assert select(completed_ci()) == CI_SHA
    assert select({}, "workflow_dispatch") == WORKFLOW_SHA


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "Unrelated workflow"),
        ("event", "pull_request"),
        ("event", "pull_request_target"),
        ("event", "workflow_dispatch"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("conclusion", "cancelled"),
        ("conclusion", "skipped"),
        ("conclusion", "timed_out"),
        ("conclusion", None),
        ("head_branch", "main"),
        ("head_branch", "feature/test"),
        ("head_repository", {"full_name": "someone/hanmaum-ai"}),
        ("head_repository", None),
    ],
)
def test_ineligible_ci_never_selects_a_release(field, value):
    event = completed_ci()
    event["workflow_run"][field] = value
    assert select(event) is None


@pytest.mark.parametrize("event_name", ["workflow_run", "workflow_dispatch"])
@pytest.mark.parametrize(
    "settings",
    [
        {"repository": "someone/hanmaum-ai"},
        {"ref": "refs/heads/main"},
        {"ref": "refs/pull/21/merge"},
        {"ref": "refs/tags/v1"},
    ],
)
def test_both_paths_require_our_repository_and_dev(event_name, settings):
    assert select(completed_ci(), event_name, **settings) is None


@pytest.mark.parametrize("action", ["requested", "in_progress", None])
def test_only_completed_events_are_admitted(action):
    event = completed_ci() | {"action": action}
    assert select(event) is None


@pytest.mark.parametrize("event", [{}, {"workflow_run": None}, {"workflow_run": {}}])
def test_missing_ci_payload_does_not_fall_back_to_workflow_sha(event):
    assert select(event) is None


@pytest.mark.parametrize("event_name", ["push", "pull_request", "schedule"])
def test_other_trigger_types_are_not_admitted(event_name):
    assert select(completed_ci(), event_name) is None


@pytest.mark.parametrize("sha", [None, "", "dev", "a" * 39, "A" * 40, "a" * 40 + "\nnext=1", 1])
@pytest.mark.parametrize("event_name", ["workflow_run", "workflow_dispatch"])
def test_invalid_commit_cannot_be_written_to_outputs(sha, event_name):
    event = completed_ci()
    event["workflow_run"]["head_sha"] = sha
    with pytest.raises(ValueError, match="Invalid release SHA"):
        select(event, event_name, workflow_sha=sha)


def cli_environment(tmp_path, monkeypatch, event=None, event_name="workflow_run"):
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(completed_ci() if event is None else event), encoding="utf-8")
    output = tmp_path / "github-output"
    values = {
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_REPOSITORY": release.REPOSITORY,
        "GITHUB_REF": "refs/heads/dev",
        "GITHUB_SHA": WORKFLOW_SHA,
        "GITHUB_OUTPUT": str(output),
        "GH_TOKEN": "synthetic-token",
    }
    monkeypatch.setattr(select_release.os, "environ", values)
    return output


@pytest.mark.parametrize(
    "event_name,expected", [("workflow_run", CI_SHA), ("workflow_dispatch", WORKFLOW_SHA)]
)
def test_selected_commit_is_verified_before_emitting_output(
    tmp_path, monkeypatch, event_name, expected
):
    output = cli_environment(tmp_path, monkeypatch, event_name=event_name)
    latest = Mock()
    monkeypatch.setattr(select_release, "require_latest", latest)
    select_release.main()
    latest.assert_called_once_with(expected, "synthetic-token")
    assert output.read_text() == f"sha={expected}\n"


@pytest.mark.parametrize("event_name", ["workflow_run", "workflow_dispatch"])
def test_superseded_run_produces_no_deployment_output(tmp_path, monkeypatch, event_name):
    output = cli_environment(tmp_path, monkeypatch, event_name=event_name)
    monkeypatch.setattr(select_release, "require_latest", Mock(side_effect=release.StaleRelease()))
    select_release.main()
    assert not output.exists()


def test_failed_ci_needs_no_token_or_network_access(tmp_path, monkeypatch):
    event = completed_ci()
    event["workflow_run"]["conclusion"] = "failure"
    output = cli_environment(tmp_path, monkeypatch, event)
    monkeypatch.delenv("GH_TOKEN")
    latest = Mock()
    monkeypatch.setattr(select_release, "require_latest", latest)
    select_release.main()
    latest.assert_not_called()
    assert not output.exists()


@pytest.mark.parametrize("failure", ["network", "malformed-json", "wrong-shape", "invalid-sha"])
def test_selector_cli_fails_closed_without_exposing_event_or_token(
    tmp_path, monkeypatch, capsys, failure
):
    output = cli_environment(tmp_path, monkeypatch)
    fetch = Mock(side_effect=OSError("synthetic-token"))
    monkeypatch.setattr(release.urllib.request, "urlopen", fetch)
    event_file = Path(select_release.os.environ["GITHUB_EVENT_PATH"])
    if failure == "malformed-json":
        event_file.write_text("synthetic-private-event", encoding="utf-8")
    elif failure == "wrong-shape":
        event_file.write_text(
            '{"action": "completed", "workflow_run": "synthetic-private-event"}', encoding="utf-8"
        )
    elif failure == "invalid-sha":
        event = completed_ci()
        event["workflow_run"]["head_sha"] = "synthetic-private-event"
        event_file.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        runpy.run_path(select_release.__file__, run_name="__main__")
    assert error.value.code == "Release selection failed; no deployment authorized"
    assert not output.exists()
    assert "synthetic-" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "event_name,initialize,expected",
    [
        ("workflow_run", "true", False),
        ("workflow_run", "false", False),
        ("workflow_dispatch", "true", True),
        ("workflow_dispatch", "false", False),
        ("workflow_dispatch", None, False),
        ("push", "true", False),
    ],
)
def test_metadata_uses_selected_commit_and_initialization_is_manual_only(
    event_name, initialize, expected
):
    values = {
        "RELEASE_SHA": CI_SHA,
        "GITHUB_SHA": WORKFLOW_SHA,
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_ACTOR": "test-user",
        "AI_IMAGE": "ghcr.io/team-hanmaum/hanmaum-ai@sha256:" + "c" * 64,
        "AI_MEMORY_LIMIT": "512m",
        "INITIALIZE_AI": initialize,
    }
    info = release.metadata(values)
    assert info["sha"] == CI_SHA and info["id"] == f"{CI_SHA}-123-2"
    assert info["initialize"] is expected
    # An absent selection must not silently use GITHUB_SHA, even for a manual release.
    del values["RELEASE_SHA"]
    with pytest.raises(KeyError):
        release.metadata(values)


def test_workflow_propagates_selected_sha_and_only_eligible_jobs_receive_write_permissions():
    # BaseLoader keeps GitHub's YAML `on` key as a string, not YAML 1.1's boolean True.
    workflow = yaml.load(
        (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    ci = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    assert workflow["on"]["workflow_run"] == {
        "workflows": [ci["name"]],
        "branches": ["dev"],
        "types": ["completed"],
    }
    assert workflow["on"]["workflow_dispatch"]["inputs"]["initialize_ai"]["default"] == "false"
    assert workflow["permissions"] == {"contents": "read"}
    prepare, build, deploy = (workflow["jobs"][key] for key in ("prepare", "build", "deploy"))
    assert "permissions" not in prepare and "environment" not in prepare
    assert prepare["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    assert prepare["steps"][1]["run"] == "python3 -m deploy.select_release"
    assert build["needs"] == "prepare" and build["if"] == "needs.prepare.outputs.sha != ''"
    assert deploy["needs"] == ["prepare", "build"]
    assert "concurrency" not in workflow and "concurrency" not in prepare
    assert deploy["concurrency"]["cancel-in-progress"] == "false"
    assert deploy["concurrency"]["queue"] == "max"
    assert deploy["environment"] == "production"
    for job in (build, deploy):
        assert job["env"]["RELEASE_SHA"] == "${{ needs.prepare.outputs.sha }}"
        assert job["steps"][0]["with"]["ref"] == "${{ env.RELEASE_SHA }}"
        assert job["steps"][1]["run"] == "python3 deploy/release.py latest"
        assert all(
            step.get("with", {}).get("persist-credentials") != "true" for step in job["steps"]
        )
        assert "GITHUB_SHA" not in json.dumps(job) and "github.sha" not in json.dumps(job)
    assert "needs.prepare.outputs.sha" in build["env"]["IMAGE_TAG"]
    assert "github.event_name == 'workflow_dispatch'" in deploy["env"]["INITIALIZE_AI"]
    upload = (ROOT / "deploy/send-release.sh").read_text()
    assert 'git archive "$RELEASE_SHA" deploy' in upload
    assert '"$(git rev-parse HEAD)" == "$RELEASE_SHA"' in upload
    assert "GITHUB_SHA" not in upload
