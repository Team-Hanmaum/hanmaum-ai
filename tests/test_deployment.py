"""Deployment invariants, using only synthetic secrets and fake external operations."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from deploy import firewall, host, release, renew_tls


def environment():
    return {
        "AI_INTERNAL_API_KEY": "internal$literal" * 3,
        "OPENAI_API_KEY": "provider-only-test",
        "AI_V2_HMAC_KEYS": json.dumps({"v1": "receipt-test" * 4}),
        "AI_V2_ACTIVE_HMAC_KEY_VERSION": "v1",
    }


def test_raw_secrets_round_trip_without_shell_evaluation(tmp_path):
    values = release.runtime_values(environment())
    path = tmp_path / "runtime.env"
    release.write_private(path, "".join(f"{k}={v}\n" for k, v in values.items()))
    assert release.read_runtime(path) == values
    with pytest.raises(FileExistsError):
        release.write_private(path, "overwrite")


@pytest.mark.parametrize(
    "key,value",
    [
        ("AI_INTERNAL_API_KEY", "short"),
        ("AI_INTERNAL_API_KEY", "x" * 32 + "\nENV=bad"),
        ("OPENAI_API_KEY", "internal$literal" * 3),
        ("OPENAI_API_KEY", "key with spaces"),
        ("AI_V2_HMAC_KEYS", "{}"),
        ("AI_V2_HMAC_KEYS", '{"v1":"short"}'),
        ("AI_V2_HMAC_KEYS", '{"v1":32}'),
        ("AI_V2_HMAC_KEYS", json.dumps({"v1": "internal$literal" * 3})),
        ("AI_V2_ACTIVE_HMAC_KEY_VERSION", "missing"),
        ("AI_V2_MODEL", "../arbitrary"),
        ("AI_V2_MAX_OUTPUT_TOKENS", "32769"),
    ],
)
def test_invalid_runtime_config_is_rejected_without_echoing_values(key, value):
    values = environment() | {key: value}
    with pytest.raises(ValueError) as error:
        release.runtime_values(values)
    assert values["AI_INTERNAL_API_KEY"] not in str(error.value)


@pytest.mark.parametrize("key", release.STABLE)
def test_stable_key_change_requires_separate_rotation(key):
    previous = release.runtime_values(environment())
    with pytest.raises(ValueError, match="rotation"):
        release.compare_stable(previous, previous | {key: "changed"})
    release.compare_stable(previous, previous | {"OPENAI_API_KEY": "rotated-provider-key"})


def test_runtime_file_cannot_enable_stub_or_change_storage(tmp_path):
    values = release.runtime_values(environment())
    values["AI_V2_PROVIDER"] = "stub"
    path = tmp_path / "runtime.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    with pytest.raises(ValueError, match="production"):
        release.read_runtime(path)


def test_be_key_matches_generated_quoted_format(tmp_path):
    candidate = release.runtime_values(environment())
    path = tmp_path / "be.env"
    encoded = json.dumps(candidate["AI_INTERNAL_API_KEY"]).replace("$", "$$")
    path.write_text(f"UNRELATED=value\nAI_INTERNAL_API_KEY={encoded}\n")
    host.check_be_key(candidate, path)
    with pytest.raises(ValueError, match="match"):
        host.check_be_key(candidate | {"AI_INTERNAL_API_KEY": "another-key" * 4}, path)


def test_stale_release_and_network_failure_stop_deployment(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps({"object": {"sha": "a" * 40}}).encode()
    monkeypatch.setattr(release.urllib.request, "urlopen", Mock(return_value=response))
    release.require_latest("a" * 40, "synthetic-token")
    with pytest.raises(ValueError, match="Stale"):
        release.require_latest("b" * 40, "synthetic-token")
    monkeypatch.setattr(release.urllib.request, "urlopen", Mock(side_effect=OSError("token")))
    with pytest.raises(ValueError, match="Cannot verify") as error:
        release.require_latest("a" * 40, "synthetic-token")
    assert "synthetic-token" not in str(error.value)


def test_initialized_missing_store_never_recreated(tmp_path, monkeypatch):
    (tmp_path / "initialized").touch()
    operation = Mock()
    monkeypatch.setattr(host, "run", operation)
    with pytest.raises(ValueError, match="lost"):
        host.prepare_storage(tmp_path, "image", initialize=True)
    operation.assert_not_called()


def test_no_implicit_storage_initialization(tmp_path, monkeypatch):
    operation = Mock()
    monkeypatch.setattr(host, "run", operation)
    with pytest.raises(ValueError, match="explicit"):
        host.prepare_storage(tmp_path, "image", initialize=False)
    operation.assert_not_called()


@pytest.mark.parametrize("previous", [None, Path("/previous-release")])
def test_failed_apply_restores_previous_or_stops_first_candidate(tmp_path, monkeypatch, previous):
    candidate = tmp_path / "candidate"
    operation = Mock(side_effect=[RuntimeError("failed health"), "restored"])
    monkeypatch.setattr(host, "compose", operation)
    with pytest.raises(RuntimeError, match="deployment failed"):
        host.apply_release(tmp_path, candidate, previous)
    second = operation.call_args_list[1].args
    assert second[0] == (previous or candidate)
    assert second[1] == ("up" if previous else "stop")
    assert json.loads((tmp_path / "deployment-state.json").read_text())["phase"] == "failed"
    assert not (tmp_path / "current").exists()


def test_failed_recovery_leaves_explicit_blocking_state(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "compose", Mock(side_effect=RuntimeError("failure")))
    with pytest.raises(RuntimeError, match="recovery failed"):
        host.apply_release(tmp_path, tmp_path / "candidate", None)
    state = json.loads((tmp_path / "deployment-state.json").read_text())
    assert state["phase"] == "recovery-required"


def test_public_or_wrong_shared_network_is_rejected(monkeypatch):
    monkeypatch.setattr(
        host,
        "run",
        Mock(side_effect=[host.NETWORK, json.dumps([{"Driver": "bridge", "Internal": False}])]),
    )
    with pytest.raises(ValueError, match="internal bridge"):
        host.ensure_network(initialize=False)


def test_firewall_cleanup_keeps_an_existing_user_rule(tmp_path, monkeypatch):
    calls = Mock()
    monkeypatch.setattr(firewall, "aws", calls)
    monkeypatch.setattr(firewall, "ssh_rules", lambda *_: [{"cidrs": ["8.8.8.8/32"]}])
    state = tmp_path / "state.json"
    firewall.open_rule(state, "region", "instance", "8.8.8.8")
    firewall.close_rule(state)
    calls.assert_not_called()


def test_firewall_cleanup_after_failed_open_uses_exact_owned_source(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    calls = Mock(side_effect=[RuntimeError("response lost"), {}])
    monkeypatch.setattr(firewall, "aws", calls)
    monkeypatch.setattr(firewall, "ssh_rules", lambda *_: [])
    monkeypatch.setattr(firewall, "wait_for_rule", lambda *_: None)
    with pytest.raises(RuntimeError):
        firewall.open_rule(state, "region", "instance", "8.8.8.8")
    firewall.close_rule(state)
    assert calls.call_args.args[1] == "close-instance-public-ports"
    assert calls.call_args.kwargs["portInfo"]["cidrs"] == ["8.8.8.8/32"]


@pytest.mark.parametrize("source", ["0.0.0.0/0", "::/0", "8.8.8.0/24"])
def test_firewall_never_requests_wide_access(source):
    with pytest.raises(ValueError):
        firewall.port_info(source)


@pytest.mark.parametrize("phase", ["applying", "recovery-required"])
def test_tls_maintenance_does_not_override_interrupted_release(tmp_path, monkeypatch, phase):
    (tmp_path / "deployment-state.json").write_text(json.dumps({"phase": phase}))
    operation = Mock()
    monkeypatch.setattr(renew_tls, "current_release", operation)
    with pytest.raises(ValueError, match="interrupted"):
        renew_tls.renew(tmp_path)
    operation.assert_not_called()


def test_tls_with_long_validity_is_not_restarted(tmp_path, monkeypatch):
    monkeypatch.setattr(renew_tls, "current_release", lambda _: tmp_path / "release")
    monkeypatch.setattr(renew_tls, "run", Mock())
    monkeypatch.setattr(renew_tls.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    verify = Mock()
    recreate = Mock()
    monkeypatch.setattr(renew_tls, "verify_tls", verify)
    monkeypatch.setattr(renew_tls, "compose", recreate)
    renew_tls.renew(tmp_path)
    verify.assert_called_once_with(tmp_path)
    recreate.assert_not_called()
