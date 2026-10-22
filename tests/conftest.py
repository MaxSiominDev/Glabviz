from __future__ import annotations

import socket
from pathlib import Path

import pytest
import streamlit as st

from glabviz.config import DATA_DIR_ENV


@pytest.fixture(autouse=True)
def clear_streamlit_caches():
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect

    def guarded(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if host in ("127.0.0.1", "::1", "localhost"):
            return original_connect(self, address)  # type: ignore[arg-type]
        raise RuntimeError(f"network access is blocked in tests: {host}")

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "data"
    monkeypatch.setenv(DATA_DIR_ENV, str(directory))
    return directory
