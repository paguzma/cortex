from pathlib import Path

import pytest


def write_note(vault: Path, folder: str, stem: str, links=(), area: str | None = None,
               kind: str = "project") -> Path:
    path = vault / folder / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    area_line = f"area: {area}\n" if area else ""
    rel = "\n".join(f"- relacionado_con [[{l}]]" for l in links)
    path.write_text(f"---\ntitle: {stem}\ntype: {kind}\nstatus: active\n{area_line}---\n\n"
                    f"Nota {stem}.\n\n## Relaciones\n{rel}\n")
    return path


def cluster_of(vault: Path, prefix: str, size: int, area: str | None = None, extra=()):
    """Racimo denso: cada nota enlaza a las dos siguientes."""
    names = [f"{prefix}-{i}" for i in range(size)]
    for i, name in enumerate(names):
        links = [names[(i + 1) % size], names[(i + 2) % size], *extra]
        write_note(vault, "entities/projects", name, links, area)
    return names


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "INDEX.md").write_text("# INDEX\n")
    return root
