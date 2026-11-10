"""Resolve the token that the glab CLI would use for a host.

Sources, in order of authority:

1. `glab auth status --hostname <host> --show-token` - whatever glab itself
   resolves is by definition the working token (its config file and keyring
   entries included), and duplicate keychain items cannot break the choice.
2. the keyring library (same framework glab uses for keyring entries).
3. the `security` CLI as a last fallback; it may pick a stale duplicate.

Reading is always an explicit user action in the UI; the value is never
logged or persisted by this module.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from urllib.parse import urlsplit

_GLAB_TIMEOUT_S = 30.0
_SECURITY_TIMEOUT_S = 60.0
_TOKEN_LINE = re.compile(r"token[^:]*:\s*(\S+)\s*$", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def keychain_service(host: str) -> str:
    return f"glab:{host}:token"


def hostname_of(base_url: str) -> str | None:
    value = base_url.strip()
    if not value:
        return None
    if "://" not in value:
        value = f"https://{value}"
    host = urlsplit(value).hostname
    return host.lower() if host else None


def read_glab_token(base_url: str) -> str | None:
    host = hostname_of(base_url)
    if host is None:
        return None
    token = _read_via_glab_cli(host)
    if token:
        return token
    service = keychain_service(host)
    token = _read_via_keyring_lib(service)
    if token:
        return token
    return _read_via_security_cli(service)


def _read_via_glab_cli(host: str) -> str | None:
    glab = shutil.which("glab")
    if glab is None:
        return None
    env = {**os.environ, "NO_COLOR": "1"}
    try:
        proc = subprocess.run(
            [glab, "auth", "status", "--hostname", host, "--show-token"],
            capture_output=True,
            text=True,
            timeout=_GLAB_TIMEOUT_S,
            env=env,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    # glab prints the auth status (token line included) to stderr.
    output = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    for raw_line in output.splitlines():
        line = _ANSI.sub("", raw_line)
        match = _TOKEN_LINE.search(line)
        if not match:
            continue
        candidate = match.group(1)
        if set(candidate) == {"*"}:
            continue
        return candidate
    return None


def _read_via_keyring_lib(service: str) -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        # The keychain is an external subsystem that can fail in many ways
        # (no backend, denied access, locked keychain); any of those means
        # "no token from this source", and the next fallback takes over.
        value = keyring.get_password(service, "")
    except Exception:
        return None
    return value.strip() if isinstance(value, str) else None


def _read_via_security_cli(service: str) -> str | None:
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=_SECURITY_TIMEOUT_S,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None
