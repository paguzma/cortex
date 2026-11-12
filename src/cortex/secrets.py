"""Backends de secretos: archivo local (secrets.env) o Infisical (solo lectura)."""
import os
import re
from pathlib import Path

import httpx

from . import paths

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
INTERNAL = ("CORTEX_MCP_TOKEN", "CORTEX_PLUGINS_TOKEN")
BOOTSTRAP = ("INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET")


class SecretError(ValueError):
    pass


class ReadOnlyBackend(SecretError):
    pass


class LocalBackend:
    writable = True

    def __init__(self, path: Path):
        self.path = path

    def get_all(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        out = {}
        for line in self.path.read_text().splitlines():
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key] = value
        return out

    def names(self) -> list[str]:
        return sorted(self.get_all())

    def set(self, name: str, value: str) -> None:
        if not NAME_RE.match(name):
            raise SecretError(f"invalid name: {name!r} (use A-Z, 0-9 and _)")
        if "\n" in value or "\r" in value:
            raise SecretError("the value cannot contain line breaks")
        data = self.get_all()
        data[name] = value
        self._write(data)

    def delete(self, name: str) -> None:
        data = self.get_all()
        data.pop(name, None)
        self._write(data)

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("".join(f"{k}={v}\n" for k, v in sorted(data.items())))
        os.replace(tmp, self.path)


class InfisicalBackend:
    writable = False

    def __init__(self, url: str, client_id: str, client_secret: str, project_id: str,
                 environment: str, http: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.project_id = project_id
        self.environment = environment
        self.http = http or httpx.Client(timeout=10)

    def _token(self) -> str:
        r = self.http.post(
            f"{self.url}/api/v1/auth/universal-auth/login",
            json={"clientId": self.client_id, "clientSecret": self.client_secret},
        )
        r.raise_for_status()
        return r.json()["accessToken"]

    def get_all(self) -> dict[str, str]:
        r = self.http.get(
            f"{self.url}/api/v3/secrets/raw",
            params={"workspaceId": self.project_id, "environment": self.environment,
                    "secretPath": "/"},
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        r.raise_for_status()
        return {s["secretKey"]: s["secretValue"] for s in r.json()["secrets"]}

    def names(self) -> list[str]:
        return sorted(self.get_all())

    def set(self, name: str, value: str) -> None:
        raise ReadOnlyBackend("in Infisical mode, edit secrets in Infisical")

    def delete(self, name: str) -> None:
        raise ReadOnlyBackend("in Infisical mode, edit secrets in Infisical")


def local_backend() -> LocalBackend:
    return LocalBackend(paths.secrets_file())


def backend_from_config(cfg: dict, local: LocalBackend | None = None,
                        http: httpx.Client | None = None):
    local = local or local_backend()
    if cfg["secrets_backend"] != "infisical":
        return local
    boot = local.get_all()
    inf = cfg["infisical"]
    return InfisicalBackend(inf["url"], boot.get("INFISICAL_CLIENT_ID", ""),
                            boot.get("INFISICAL_CLIENT_SECRET", ""), inf["project_id"],
                            inf["environment"], http=http)


def resolve_env(cfg: dict, local: LocalBackend | None = None,
                http: httpx.Client | None = None) -> dict[str, str]:
    """Secretos para los procesos: nunca incluye los tokens internos ni las credenciales de arranque."""
    local = local or local_backend()
    env = local.get_all()
    if cfg["secrets_backend"] == "infisical":
        env.update(backend_from_config(cfg, local, http).get_all())
    for key in (*INTERNAL, *BOOTSTRAP):
        env.pop(key, None)
    return env
