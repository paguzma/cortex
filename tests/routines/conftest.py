import pytest

from cortex import config, paths
from cortex.gitutil import init_repo
from cortex.routines.base import Context


@pytest.fixture
def ctx(data):
    """Contexto de rutina sobre un vault con un commit inicial."""
    vault = paths.vault_dir()
    init_repo(vault)
    (vault / "INDEX.md").write_text("# INDEX\n")
    from cortex.gitutil import commit_all
    commit_all(vault, "init")
    state = paths.state_dir() / "routines" / "test"
    state.mkdir(parents=True)
    return Context(cfg=config.load(), env={}, vault=vault, archive=paths.data_dir() / "archive",
                   state=state, model="opus", claude=None)
