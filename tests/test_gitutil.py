import pytest

from cortex.gitutil import commit_all, git, init_repo


def test_commit_all_only_when_changes(tmp_path):
    init_repo(tmp_path)
    assert commit_all(tmp_path, "vacío") is False
    (tmp_path / "a.md").write_text("hola")
    assert commit_all(tmp_path, "primero") is True
    assert git(tmp_path, "log", "--format=%s") == "primero"
    assert commit_all(tmp_path, "otra vez") is False


def test_git_raises_on_error(tmp_path):
    with pytest.raises(RuntimeError):
        git(tmp_path, "log")
