"""El producto se llama cortex: ningún archivo puede mencionar el nombre del agente de Pato."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = "claud" + "io"


def test_no_forbidden_name_in_repo():
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    offenders = []
    for rel in tracked:
        path = ROOT / rel
        if FORBIDDEN in rel.lower():
            offenders.append(rel)
            continue
        try:
            if FORBIDDEN in path.read_text(errors="ignore").lower():
                offenders.append(rel)
        except (IsADirectoryError, FileNotFoundError):
            pass
    assert offenders == []
