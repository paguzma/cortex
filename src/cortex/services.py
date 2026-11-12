"""Control de los procesos supervisados por s6 (corren con el mismo usuario que la UI)."""
import subprocess
from pathlib import Path

SERVICE_DIR = Path("/run/service")


def restart(name: str, run=subprocess.run) -> bool:
    service = SERVICE_DIR / name
    if not service.exists():
        return False
    return run(["/command/s6-svc", "-r", str(service)], capture_output=True).returncode == 0
