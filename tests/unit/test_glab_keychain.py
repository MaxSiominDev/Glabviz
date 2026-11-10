import subprocess

import pytest

from glabviz.glab_keychain import hostname_of, keychain_service, read_glab_token


@pytest.fixture(autouse=True)
def no_glab_binary(monkeypatch):
    """Keep the lower-fallback tests hermetic: without this, read_glab_token
    starts by shelling out to the real glab binary."""
    monkeypatch.setattr("glabviz.glab_keychain.shutil.which", lambda name: None)


@pytest.fixture(autouse=True)
def no_keyring_lib(monkeypatch):
    """Keep the CLI-path tests hermetic: the keyring library reads the real
    keychain, so tests silence it unless they exercise it explicitly."""
    monkeypatch.setattr("keyring.get_password", lambda service, username: None)


def _result(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_success_returns_stripped_token(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _result(0, "  glpat-secret\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert read_glab_token("https://gitlab.corp.mail.ru/") == "glpat-secret"
    assert seen["cmd"] == [
        "security",
        "find-generic-password",
        "-s",
        "glab:gitlab.corp.mail.ru:token",
        "-w",
    ]


def test_missing_entry_returns_none(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _result(44, ""))
    assert read_glab_token("https://gitlab.example.com") is None


def test_empty_stdout_returns_none(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _result(0, "\n"))
    assert read_glab_token("https://gitlab.example.com") is None


def test_timeout_returns_none(monkeypatch):
    def raise_timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    assert read_glab_token("https://gitlab.example.com") is None


def test_missing_security_binary_returns_none(monkeypatch):
    def raise_missing(cmd, **kwargs):
        raise FileNotFoundError("security")

    monkeypatch.setattr(subprocess, "run", raise_missing)
    assert read_glab_token("https://gitlab.example.com") is None


def test_blank_url_skips_subprocess(monkeypatch):
    def fail_run(cmd, **kwargs):
        raise AssertionError("subprocess must not be called")

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert read_glab_token("   ") is None


def test_hostname_normalization():
    assert hostname_of("https://gitlab.corp.mail.ru/api/v4") == "gitlab.corp.mail.ru"
    assert hostname_of("GitLab.Corp.Mail.RU") == "gitlab.corp.mail.ru"
    assert hostname_of("https://gitlab.corp.mail.ru:8443") == "gitlab.corp.mail.ru"
    assert hostname_of("") is None
    assert keychain_service("gitlab.corp.mail.ru") == "glab:gitlab.corp.mail.ru:token"


def test_keyring_lib_is_tried_first(monkeypatch):
    calls = []

    def fail_run(cmd, **kwargs):
        calls.append(cmd)
        raise AssertionError("security CLI must not run when the keyring library has a token")

    monkeypatch.setattr(subprocess, "run", fail_run)
    monkeypatch.setattr("keyring.get_password", lambda service, username: " glpat-from-keyring\n")
    assert read_glab_token("https://gitlab.example.com") == "glpat-from-keyring"
    assert calls == []


def test_keyring_lib_none_falls_back_to_cli(monkeypatch):
    monkeypatch.setattr("keyring.get_password", lambda service, username: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _result(0, "glpat-cli\n"))
    assert read_glab_token("https://gitlab.example.com") == "glpat-cli"


def test_keyring_lib_error_falls_back_to_cli(monkeypatch):
    def raise_error(service, username):
        raise RuntimeError("locked keychain")

    monkeypatch.setattr("keyring.get_password", raise_error)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _result(0, "glpat-cli\n"))
    assert read_glab_token("https://gitlab.example.com") == "glpat-cli"


GLAB_OUTPUT = (
    "gitlab.corp.mail.ru\n"
    "  \x1b[32m\xe2\x9c\x93\x1b[0m Logged in to gitlab.corp.mail.ru as user (keyring)\n"
    "  \x1b[32m\xe2\x9c\x93\x1b[0m Token found in operating system keyring: glpat-from-glab\n"
)


def test_glab_cli_source_parses_show_token(monkeypatch):
    monkeypatch.setattr("glabviz.glab_keychain.shutil.which", lambda name: "/opt/homebrew/bin/glab")

    def fake_run(cmd, **kwargs):
        assert cmd[0].endswith("glab")
        assert cmd[1:4] == ["auth", "status", "--hostname"]
        assert "--show-token" in cmd
        return _result(0, GLAB_OUTPUT)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert read_glab_token("https://gitlab.corp.mail.ru") == "glpat-from-glab"


def test_glab_cli_masked_token_falls_through(monkeypatch):
    monkeypatch.setattr("glabviz.glab_keychain.shutil.which", lambda name: "glab")
    masked = GLAB_OUTPUT.replace("glpat-from-glab", "**********")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _result(0, masked))
    monkeypatch.setattr("keyring.get_password", lambda service, username: "glpat-from-keyring")
    assert read_glab_token("https://gitlab.corp.mail.ru") == "glpat-from-keyring"


def test_glab_cli_failure_falls_through(monkeypatch):
    monkeypatch.setattr("glabviz.glab_keychain.shutil.which", lambda name: "glab")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _result(1, ""))
    monkeypatch.setattr("keyring.get_password", lambda service, username: "glpat-from-keyring")
    assert read_glab_token("https://gitlab.corp.mail.ru") == "glpat-from-keyring"


def test_glab_cli_token_on_stderr_is_parsed(monkeypatch):
    monkeypatch.setattr("glabviz.glab_keychain.shutil.which", lambda name: "glab")

    def fake_run(cmd, **kwargs):
        result = _result(0, "")
        result.stderr = (
            "gitlab.corp.mail.ru\n  ✓ Token found in operating system keyring: glpat-on-stderr\n"
        )
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert read_glab_token("https://gitlab.corp.mail.ru") == "glpat-on-stderr"
