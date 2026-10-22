from glabviz.config import DATA_DIR_ENV, DEFAULT_DATA_DIR, data_dir


def test_default_data_dir(monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert data_dir() == DEFAULT_DATA_DIR


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    assert data_dir() == tmp_path
