"""Envoltorio mínimo de git con identidad fija para los commits automáticos."""
import subprocess
from pathlib import Path

IDENTITY = ["-c", "user.name=cortex", "-c", "user.email=cortex@localhost"]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *IDENTITY, "-C", str(repo), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")


def commit_all(repo: Path, message: str) -> bool:
    git(repo, "add", "-A")
    if not git(repo, "status", "--porcelain"):
        return False
    git(repo, "commit", "-q", "-m", message)
    return True
