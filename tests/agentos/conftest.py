import pytest

AREAS = ("home", "lab", "work")


def write_vault(vault, per_area=10):
    """~30 notas en 3 dominios: una con `area:` por dominio, el resto enlazadas a ella."""
    for area in AREAS:
        hub = vault / "entities" / "projects" / f"{area}-hub.md"
        hub.parent.mkdir(parents=True, exist_ok=True)
        hub.write_text(f"---\ntitle: {area.title()} hub\ntype: project\narea: {area}\nupdated: 2026-10-01\n---\n\n"
                       f"The {area} hub. [[SCHEMA]]\n\n## Observaciones\n- [pendiente] tidy the {area} hub\n")
        for i in range(per_area - 1):
            folder = ("entities/tools", "references", "decisions", "feedback")[i % 4]
            note = vault / folder / f"{area}-note-{i}.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(f"---\ntitle: {area} note {i}\ntype: tool\n---\n\nNote {i} of {area}.\n\n"
                            f"## Relaciones\n- pertenece_a [[{area}-hub]]\n")


@pytest.fixture
def seeded_vault(ready):
    from cortex import paths
    write_vault(paths.vault_dir())
    return paths.vault_dir()
