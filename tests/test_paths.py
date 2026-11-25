from pathlib import Path

from broker import paths


def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_DATA_DIR", str(tmp_path))
    assert paths.data_dir() == tmp_path


def test_checkout_data_folder_is_used_by_default(monkeypatch):
    monkeypatch.delenv("BROKER_DATA_DIR", raising=False)
    assert (paths.data_dir() / "01a_trace.csv").is_file()


def test_falls_back_to_cwd_when_installed_outside_a_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("BROKER_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "_REPO_DATA", Path("/nonexistent"))
    monkeypatch.chdir(tmp_path)
    assert paths.data_dir() == tmp_path / "data"
