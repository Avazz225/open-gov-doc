import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from og_license_tool import keys, licenses
from og_license_tool.main import app
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def key_home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "license-home"
    monkeypatch.setenv("OG_LICENSE_HOME", str(home))
    monkeypatch.delenv("DMS_LICENSE_PUBLIC_KEY_PEM", raising=False)
    result = runner.invoke(app, ["keygen", "--bits", "2048"])
    assert result.exit_code == 0, result.output
    return home


def _issue(*extra: str) -> str:
    result = runner.invoke(
        app,
        [
            "issue",
            "--licensee",
            "Stadt Musterhausen",
            "--days",
            "30",
            "--user-model",
            "named",
            "--max-users",
            "25",
            "--component",
            "workflow-service",
            "--yes",
            *extra,
        ],
    )
    assert result.exit_code == 0, result.output
    return result.stdout.strip().splitlines()[-1]


def test_keygen_writes_a_private_key_only_the_owner_can_read(key_home):
    private = key_home / keys.PRIVATE_KEY_NAME
    assert private.stat().st_mode & 0o777 == 0o600
    assert (key_home / keys.PUBLIC_KEY_NAME).read_text().startswith("-----BEGIN PUBLIC KEY-----")
    # A second keygen would invalidate issued licences: refused without --force.
    again = runner.invoke(app, ["keygen", "--bits", "2048"])
    assert again.exit_code == 2
    assert "--force" in again.output


def test_keygen_refuses_a_git_work_tree(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    result = runner.invoke(app, ["keygen", "--out", str(repo / "keys"), "--bits", "2048"])
    assert result.exit_code == 2
    assert "Git-Arbeitsbaum" in result.output
    assert not (repo / "keys").exists()


def test_issue_then_verify_is_valid_and_logged(key_home):
    token = _issue("--installation-id", "amt-42")

    verified = runner.invoke(app, ["verify", token, "--installation-id", "amt-42"])
    assert verified.exit_code == 0, verified.output
    assert "GÜLTIG" in verified.output
    assert "workflow-service" in verified.output

    [entry] = [
        json.loads(line) for line in (key_home / keys.ISSUED_LOG_NAME).read_text().splitlines()
    ]
    assert entry["licensee"] == "Stadt Musterhausen"
    assert entry["sha256"] == licenses.token_fingerprint(token)
    listed = runner.invoke(app, ["issued"])
    assert entry["license_id"] in listed.output


def test_claims_match_what_license_service_reads(key_home):
    token = _issue("--storage-gb", "500", "--not-before", "2026-01-01")
    _header, claims = licenses.decode_unverified(token)
    assert claims["user_model"] == "named"
    assert claims["max_users"] == 25
    assert claims["storage_limit_gb"] == 500
    assert claims["document_limit"] is None
    assert claims["licensed_components"] == ["workflow-service"]
    assert claims["nbf"] == int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    assert claims["exp"] > claims["iat"]
    assert "installation_id" not in claims


def test_verify_reports_wrong_installation_expiry_and_signature(key_home, tmp_path):
    token = _issue("--installation-id", "amt-42")
    public = (key_home / keys.PUBLIC_KEY_NAME).read_text()

    other = licenses.verify(token, public_key_pem=public, installation_id="amt-7")
    assert not other.valid and "andere Installation" in other.problems[0]

    later = licenses.verify(
        token, public_key_pem=public, now=datetime.now(UTC) + timedelta(days=60)
    )
    assert later.signature_ok and later.problems == ["abgelaufen"]

    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload[:-2]}AA.{signature}"
    result = runner.invoke(app, ["verify", tampered])
    assert result.exit_code == 1
    assert "UNGÜLTIG" in result.output


def test_verify_accepts_the_previous_key_during_rotation(key_home, tmp_path, monkeypatch):
    old_token = _issue()
    old_public = key_home / keys.PUBLIC_KEY_NAME
    saved = tmp_path / "old-public.pem"
    saved.write_text(old_public.read_text())
    runner.invoke(app, ["keygen", "--bits", "2048", "--force"])

    without = runner.invoke(app, ["verify", old_token])
    assert without.exit_code == 1
    during = runner.invoke(app, ["verify", old_token, "--previous-public-key", str(saved)])
    assert during.exit_code == 0, during.output
    assert "Vorgänger-Schlüssel" in during.output


def test_issue_insists_on_an_explicit_component_choice_and_one_validity(key_home):
    base = ["issue", "--licensee", "X", "--user-model", "concurrent", "--yes"]
    no_choice = runner.invoke(app, [*base, "--days", "10"])
    assert no_choice.exit_code == 2 and "Komponenten" in no_choice.output
    both = runner.invoke(app, [*base, "--days", "10", "--until", "2030-01-01", "--all-components"])
    assert both.exit_code == 2
    everything = runner.invoke(app, [*base, "--until", "2030-01-01", "--all-components"])
    assert everything.exit_code == 0, everything.output
    _header, claims = licenses.decode_unverified(everything.stdout.strip().splitlines()[-1])
    assert claims["licensed_components"] is None
    assert claims["exp"] == int(datetime(2030, 1, 1, 23, 59, 59, tzinfo=UTC).timestamp())


def test_inspect_reads_without_verifying(key_home):
    token = _issue()
    result = runner.invoke(app, ["inspect", token])
    assert result.exit_code == 0
    assert "Stadt Musterhausen" in result.output
    assert "Signatur nicht geprüft" in result.output
    raw = runner.invoke(app, ["inspect", "--json", token])
    assert json.loads(raw.output)["header"]["alg"] == "RS256"


def test_a_private_key_others_can_read_is_refused(key_home):
    private = key_home / keys.PRIVATE_KEY_NAME
    os.chmod(private, 0o644)
    with pytest.raises(keys.KeyError_, match="chmod 600"):
        keys.load_private_key(None, dev=False)


def test_a_private_key_tracked_by_git_is_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    leaked = repo / "vendor-key.txt"
    leaked.write_bytes(b"irrelevant")
    os.chmod(leaked, 0o600)
    with pytest.raises(keys.KeyError_, match="nicht ignoriert"):
        keys.load_private_key(leaked, dev=False)
