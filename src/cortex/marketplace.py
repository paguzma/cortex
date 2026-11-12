"""Marketplace de plugins servido por git HTTP: plugin base `cortex` y `my-skills` del usuario."""
import functools
import io
import json
import os
import re
import shutil
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from .gitutil import commit_all, git, init_repo

BASE = "cortex"
USER = "my-skills"
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_ZIP = 5 * 1024 * 1024
MAX_UNZIPPED = 20 * 1024 * 1024
# Las escrituras llegan desde hilos de la UI (asyncio.to_thread): sin esto, un doble clic
# en «Save» choca en el index.lock de git y puede dejar plugin.json a medias.
_LOCK = threading.RLock()

MARKETPLACE = {
    "name": "cortex",
    "owner": {"name": "cortex"},
    "plugins": [
        {"name": BASE, "source": f"./plugins/{BASE}",
         "description": "Memoria cortex: skills /mem-* y el MCP de esta instancia"},
        {"name": USER, "source": f"./plugins/{USER}",
         "description": "Tus skills, cargadas desde la UI de cortex"},
    ],
}


class SkillError(ValueError):
    pass


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        raise SkillError("SKILL.md must start with a frontmatter block (---)")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillError("the frontmatter block is not closed (---)")
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"invalid frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillError("invalid frontmatter")
    return data


def validate_skill_md(text: str, expected_name: str | None = None) -> str:
    fm = parse_frontmatter(text)
    name = str(fm.get("name", "")).strip()
    description = str(fm.get("description", "")).strip()
    if not SKILL_NAME_RE.match(name):
        raise SkillError("name must use lowercase letters, digits and dashes (max 64)")
    if not description:
        raise SkillError("description is missing")
    if expected_name and name != expected_name:
        raise SkillError(f"name must be {expected_name!r}")
    return name


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _read_version(plugin_json: Path) -> str:
    return json.loads(plugin_json.read_text())["version"]


def _write_version(plugin_json: Path, version: str) -> None:
    data = json.loads(plugin_json.read_text())
    data["version"] = version
    # temporal con nombre único en el mismo directorio: un «plugin.tmp» huérfano entraría al
    # repo del marketplace con el siguiente commit_all
    fd, tmp = tempfile.mkstemp(dir=plugin_json.parent, prefix=".plugin-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.chmod(tmp, plugin_json.stat().st_mode & 0o777)
        os.replace(tmp, plugin_json)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _locked(method):
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return method(*args, **kwargs)
    return wrapper


def bump_patch(plugin_json: Path) -> str:
    major, minor, patch = version_tuple(_read_version(plugin_json))
    version = f"{major}.{minor}.{patch + 1}"
    _write_version(plugin_json, version)
    return version


def render_mcp_json(base_url: str) -> dict:
    return {"mcpServers": {"cortex": {
        "type": "http",
        "url": base_url.rstrip("/") + "/mcp",
        "headers": {"Authorization": "Bearer ${CORTEX_MCP_TOKEN}"},
    }}}


@dataclass
class Market:
    repo: Path
    public: Path

    @property
    def base_dir(self) -> Path:
        return self.repo / "plugins" / BASE

    @property
    def user_dir(self) -> Path:
        return self.repo / "plugins" / USER

    def _skills_dir(self, plugin: str) -> Path:
        return self.repo / "plugins" / plugin / "skills"

    @_locked
    def seed(self) -> bool:
        manifest = self.repo / ".claude-plugin" / "marketplace.json"
        if manifest.exists():
            return False
        if not (self.repo / ".git").exists():
            init_repo(self.repo)
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(MARKETPLACE, indent=2, ensure_ascii=False) + "\n")
        (self.user_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (self.user_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"name": USER, "description": "Skills propias", "version": "0.1.0",
             "author": {"name": "cortex"}}, indent=2) + "\n")
        self._skills_dir(USER).mkdir(parents=True, exist_ok=True)
        (self._skills_dir(USER) / ".gitkeep").touch()
        commit_all(self.repo, "chore: marketplace inicial")
        return True

    @_locked
    def sync_base(self, assets_plugin: Path, base_url: str) -> bool:
        """Mantiene el plugin base al día con la imagen y con la URL de la instancia.

        Cada cambio publicado sube la versión: sin eso los clientes no lo toman.
        """
        asset_version = _read_version(assets_plugin / ".claude-plugin" / "plugin.json")
        marker = self.base_dir / ".asset-version"
        plugin_json = self.base_dir / ".claude-plugin" / "plugin.json"
        current_asset = marker.read_text().strip() if marker.exists() else "0.0.0"
        published = _read_version(plugin_json) if plugin_json.exists() else "0.0.0"
        changed = False

        if version_tuple(asset_version) > version_tuple(current_asset):
            if self.base_dir.exists():
                shutil.rmtree(self.base_dir)
            shutil.copytree(assets_plugin, self.base_dir)
            marker.write_text(asset_version + "\n")
            if version_tuple(asset_version) <= version_tuple(published):
                _write_version(plugin_json, published)
                bump_patch(plugin_json)
            changed = True

        mcp_path = self.base_dir / ".mcp.json"
        rendered = json.dumps(render_mcp_json(base_url), indent=2) + "\n"
        if not mcp_path.exists() or mcp_path.read_text() != rendered:
            mcp_path.write_text(rendered)
            if not changed:
                bump_patch(plugin_json)
            changed = True

        if changed:
            commit_all(self.repo, f"chore: plugin cortex {_read_version(plugin_json)}")
            self.publish()
        return changed

    @_locked
    def publish(self) -> None:
        """Deja una copia bare servible por HTTP «dumb» (sin procesos extra)."""
        if not (self.public / "HEAD").exists():
            self.public.parent.mkdir(parents=True, exist_ok=True)
            git(self.public.parent, "clone", "-q", "--bare", str(self.repo), str(self.public))
        else:
            git(self.public, "fetch", "-q", "--force", str(self.repo),
                "+refs/heads/*:refs/heads/*")
        git(self.public, "update-server-info")

    def list_skills(self) -> list[dict]:
        out = []
        for plugin in (BASE, USER):
            directory = self._skills_dir(plugin)
            if not directory.exists():
                continue
            for skill in sorted(directory.iterdir()):
                md = skill / "SKILL.md"
                if not md.exists():
                    continue
                try:
                    description = str(parse_frontmatter(md.read_text()).get("description", ""))
                except SkillError:
                    description = "(invalid frontmatter)"
                out.append({"name": skill.name, "description": description,
                            "plugin": plugin, "editable": plugin == USER})
        return out

    def _user_skill(self, name: str) -> Path:
        if not SKILL_NAME_RE.match(name):
            raise SkillError("invalid skill name")
        return self._skills_dir(USER) / name

    def _commit_user(self, message: str) -> None:
        bump_patch(self.user_dir / ".claude-plugin" / "plugin.json")
        commit_all(self.repo, message)
        self.publish()

    @_locked
    def add_skill_zip(self, data: bytes) -> str:
        if len(data) > MAX_ZIP:
            raise SkillError("the zip is larger than 5 MB")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise SkillError("not a valid zip file") from exc
        names = [n for n in archive.namelist()
                 if not n.endswith("/") and not n.startswith("__MACOSX/")
                 and PurePosixPath(n).name != ".DS_Store"]
        for n in names:
            if n.startswith("/") or ".." in PurePosixPath(n).parts or "\\" in n:
                raise SkillError("the zip contains unsafe paths")
        if sum(archive.getinfo(n).file_size for n in names) > MAX_UNZIPPED:
            raise SkillError("the unzipped skill is larger than 20 MB")
        if "SKILL.md" in names:
            prefix = ""
        else:
            tops = {n.split("/", 1)[0] for n in names}
            top = next(iter(tops)) if len(tops) == 1 else None
            if not top or f"{top}/SKILL.md" not in names:
                raise SkillError("SKILL.md not found at the root of the zip")
            prefix = f"{top}/"
        try:
            text = archive.read(prefix + "SKILL.md").decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillError("SKILL.md must be UTF-8") from exc
        name = validate_skill_md(text)
        dest = self._user_skill(name)
        if dest.exists():
            shutil.rmtree(dest)
        for n in names:
            out = dest / n[len(prefix):]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(archive.read(n))
        self._commit_user(f"feat(my-skills): {name}")
        return name

    def read_skill_md(self, name: str) -> str:
        md = self._user_skill(name) / "SKILL.md"
        if not md.exists():
            raise SkillError(f"skill {name!r} does not exist")
        return md.read_text()

    @_locked
    def save_skill_md(self, name: str, text: str, create: bool = False) -> None:
        dest = self._user_skill(name)
        if not dest.exists() and not create:
            raise SkillError(f"skill {name!r} does not exist")
        validate_skill_md(text, expected_name=name)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(text)
        self._commit_user(f"feat(my-skills): {name}")

    @_locked
    def delete_skill(self, name: str) -> None:
        dest = self._user_skill(name)
        if not dest.exists():
            raise SkillError(f"skill {name!r} does not exist")
        shutil.rmtree(dest)
        self._commit_user(f"chore(my-skills): remove {name}")
