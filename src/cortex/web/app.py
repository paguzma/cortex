"""UI de configuración de cortex (/setup) y endpoints de forward_auth para Caddy."""
import asyncio
import functools
import ipaddress
import json
import subprocess
from datetime import datetime
import zoneinfo
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from .. import auth, checks, config, paths, seed, services
from ..routines import report as routine_report
from ..routines import runner
from ..routines.schedule import validate_schedule, write_crontab
from . import githttp
from ..marketplace import SKILL_NAME_RE, Market, SkillError
from ..secrets import (BOOTSTRAP, INTERNAL, LocalBackend, SecretError, backend_from_config,
                       resolve_env)

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
HIDDEN = set(INTERNAL) | set(BOOTSTRAP)
STEPS = {1: "Instance", 2: "Secrets", 3: "Claude", 4: "Git backup"}
NEW_SKILL = "---\nname: {name}\ndescription: What it does and when to use it.\n---\n\n# {name}\n\n"


def market() -> Market:
    return Market(paths.plugins_dir(), paths.plugins_public_dir())


def local() -> LocalBackend:
    return LocalBackend(paths.secrets_file())


def go(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def render(request: Request, template: str, status: int = 200, /, **ctx) -> Response:
    ctx.setdefault("msg", None)
    ctx.setdefault("ok", None)
    ctx.update(cfg=config.load(), logged=request.session.get("admin") is True)
    return templates.TemplateResponse(request, template, ctx, status_code=status)


def protected(handler):
    @functools.wraps(handler)
    async def wrapper(request: Request) -> Response:
        if not config.load()["admin"]["password_hash"]:
            return go("/setup/claim")
        if request.session.get("admin") is not True:
            return go("/setup/login")
        return await handler(request)
    return wrapper


def field(form, name: str, default: str = "") -> str:
    value = form.get(name, default)
    return value.strip() if isinstance(value, str) else default


# --- reclamo, login y forward_auth ---------------------------------------------------------

async def root(request: Request) -> Response:
    return go("/setup")


async def claim(request: Request) -> Response:
    if config.load()["admin"]["password_hash"]:
        return go("/setup/login")
    if request.method == "GET":
        return render(request, "claim.html")
    form = await request.form()
    code_file = paths.setup_code_file()
    stored = code_file.read_text().strip() if code_file.exists() else ""
    if not auth.same(field(form, "code").upper(), stored):
        return render(request, "claim.html", 400, ok=False,
                      msg="That code does not match the one in the container logs.")
    password = form.get("password", "")
    if len(password) < auth.MIN_PASSWORD:
        return render(request, "claim.html", 400, ok=False,
                      msg=f"The password needs at least {auth.MIN_PASSWORD} characters.")
    if password != form.get("password2", ""):
        return render(request, "claim.html", 400, ok=False, msg="The passwords do not match.")
    cfg = config.load()
    cfg["admin"]["password_hash"] = auth.hash_password(password)
    config.save(cfg)
    code_file.unlink(missing_ok=True)
    request.session["admin"] = True
    return go("/setup")


async def login(request: Request) -> Response:
    hashed = config.load()["admin"]["password_hash"]
    if not hashed:
        return go("/setup/claim")
    if request.method == "GET":
        return render(request, "login.html", next=safe_next(request.query_params.get("next")))
    form = await request.form()
    if auth.verify_password(hashed, form.get("password", "")):
        request.session["admin"] = True
        return go(safe_next(form.get("next")))
    await asyncio.sleep(1)
    return render(request, "login.html", 401, ok=False, msg="Wrong password.",
                  next=safe_next(form.get("next")))


async def logout(request: Request) -> Response:
    request.session.clear()
    return go("/setup/login")


async def auth_session(request: Request) -> Response:
    """forward_auth de /os: la misma sesión de admin que /setup."""
    if request.session.get("admin") is True and config.load()["admin"]["password_hash"]:
        return Response(status_code=204)
    return RedirectResponse("/setup/login?next=/os/", status_code=303)


def safe_next(value: str | None) -> str:
    """Solo rutas internas conocidas: nunca un redirect a otro sitio."""
    if value and value.startswith(("/os/", "/setup")) and "//" not in value and "\\" not in value:
        return value
    return "/setup"


async def auth_mcp(request: Request) -> Response:
    if auth.check_bearer(request.headers.get("authorization"),
                         local().get_all().get("CORTEX_MCP_TOKEN")):
        return Response(status_code=204)
    return Response(status_code=401, headers={"WWW-Authenticate": 'Bearer realm="cortex"'})


async def plugins_git(request: Request) -> Response:
    if not auth.check_basic(request.headers.get("authorization"),
                            local().get_all().get("CORTEX_PLUGINS_TOKEN")):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="cortex"'})
    return await githttp.serve(request, paths.plugins_public_dir())


# --- inicio y asistente --------------------------------------------------------------------

@protected
async def home(request: Request) -> Response:
    cfg = config.load()
    if not cfg["setup_complete"]:
        return go("/setup/wizard/1")
    rows = await asyncio.to_thread(checks.health, cfg)
    return render(request, "home.html", rows=rows)


def render_step(request: Request, step: int, status: int = 200, **ctx) -> Response:
    cfg = config.load()
    if step == 3 and cfg["secrets_backend"] == "local":
        ctx["credential"] = checks.claude_credential(local().get_all())
    if step == 4:
        ctx["public_key"] = checks.deploy_public_key(paths.deploy_key_file())
    return render(request, "wizard.html", status, step=step, steps=STEPS, **ctx)


@protected
async def wizard(request: Request) -> Response:
    step = request.path_params["step"]
    if step not in STEPS:
        return go("/setup/wizard/1")
    if request.method == "GET":
        return render_step(request, step)
    form = await request.form()
    handler = {1: step_instance, 2: step_secrets, 3: step_claude, 4: step_backup}[step]
    return await handler(request, form)


async def step_instance(request: Request, form) -> Response:
    name = field(form, "name") or "cortex"
    timezone = field(form, "timezone")
    base_url = field(form, "base_url").rstrip("/")
    try:
        zoneinfo.ZoneInfo(timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return render_step(request, 1, 400, ok=False, msg=f"Unknown time zone: {timezone!r}")
    if not base_url.startswith(("http://", "https://")):
        return render_step(request, 1, 400, ok=False,
                           msg="The base URL must start with http:// or https://")
    cfg = config.load()
    timezone_changed = cfg["instance"]["timezone"] != timezone
    cfg["instance"].update(name=name, timezone=timezone, base_url=base_url)
    config.save(cfg)
    if timezone_changed:
        services.restart("supercronic")
    await asyncio.to_thread(market().sync_base, seed.assets() / "plugin", base_url)
    return go("/setup/wizard/2")


async def step_secrets(request: Request, form) -> Response:
    cfg = config.load()
    backend = field(form, "backend", "local")
    backend = backend if backend in ("local", "infisical") else "local"
    loc = local()
    if backend == "infisical":
        cfg["infisical"].update(url=field(form, "url").rstrip("/"),
                                project_id=field(form, "project_id"),
                                environment=field(form, "environment") or "prod")
        for form_name, key in (("client_id", "INFISICAL_CLIENT_ID"),
                               ("client_secret", "INFISICAL_CLIENT_SECRET")):
            if field(form, form_name):
                loc.set(key, field(form, form_name))
    cfg["secrets_backend"] = backend
    config.save(cfg)
    if field(form, "action") == "test":
        if backend != "infisical":
            return render_step(request, 2, ok=True, msg="Local mode does not need a test.")
        boot, inf = loc.get_all(), cfg["infisical"]
        ok, detail = await asyncio.to_thread(
            checks.check_infisical, inf["url"], boot.get("INFISICAL_CLIENT_ID", ""),
            boot.get("INFISICAL_CLIENT_SECRET", ""), inf["project_id"], inf["environment"])
        return render_step(request, 2, ok=ok, msg=detail)
    return go("/setup/wizard/3")


async def step_claude(request: Request, form) -> Response:
    action = field(form, "action")
    if action == "skip":
        return go("/setup/wizard/4")
    cfg = config.load()
    cfg["claude"]["model"] = field(form, "model") or "opus"
    value = field(form, "value")
    if cfg["secrets_backend"] == "local" and value:
        kind = field(form, "kind", "oauth")
        key, other = (("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY") if kind == "oauth"
                      else ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"))
        loc = local()
        loc.set(key, value)
        loc.delete(other)
        cfg["claude"]["token_loaded_at"] = date.today().isoformat() if kind == "oauth" else None
    config.save(cfg)
    if action == "test":
        try:
            env = await asyncio.to_thread(resolve_env, cfg)
        except Exception as exc:
            return render_step(request, 3, ok=False,
                               msg=f"Could not read the secrets ({type(exc).__name__})")
        ok, detail = await asyncio.to_thread(checks.check_claude, env, cfg["claude"]["model"])
        return render_step(request, 3, ok=ok, msg=detail)
    return go("/setup/wizard/4")


async def step_backup(request: Request, form) -> Response:
    cfg = config.load()
    url = field(form, "remote_url")
    cfg["git_remote"]["url"] = url
    config.save(cfg)
    action = field(form, "action")
    if url:
        await asyncio.to_thread(checks.ensure_deploy_key, paths.deploy_key_file())
    if action == "key":
        if not url:
            return render_step(request, 4, 400, ok=False, msg="Write the remote URL first.")
        return render_step(request, 4, ok=True,
                           msg="Add this public key as a deploy key with write access.")
    if action == "test":
        if not url:
            return render_step(request, 4, 400, ok=False, msg="Write the remote URL first.")
        ok, detail = await asyncio.to_thread(checks.check_git_remote, url,
                                             paths.deploy_key_file(), paths.known_hosts_file())
        return render_step(request, 4, ok=ok, msg=detail)
    cfg["setup_complete"] = True
    config.save(cfg)
    return go("/setup/connect")


# --- secretos, skills, conectar, salud -----------------------------------------------------

@protected
async def secrets_page(request: Request) -> Response:
    cfg = config.load()
    msg = ok = None
    if request.method == "POST":
        form = await request.form()
        name = field(form, "name").upper()
        try:
            if name in HIDDEN:
                raise SecretError("cortex manages that name itself")
            backend = backend_from_config(cfg)
            if field(form, "action") == "delete":
                backend.delete(name)
                msg = f"{name} deleted."
            else:
                value = form.get("value", "")
                backend.set(name, value if isinstance(value, str) else "")
                msg = f"{name} saved."
            ok = True
        except SecretError as exc:
            msg, ok = str(exc), False
    error = None
    try:
        names = [n for n in await asyncio.to_thread(backend_from_config(cfg).names)
                 if n not in HIDDEN]
    except Exception as exc:
        names, error = [], f"Could not read the secrets ({type(exc).__name__})"
    return render(request, "secrets.html", 200 if ok is not False else 400, names=names,
                  error=error, writable=cfg["secrets_backend"] == "local", msg=msg, ok=ok)


@protected
async def skills_page(request: Request) -> Response:
    return render(request, "skills.html", skills=market().list_skills())


@protected
async def skills_upload(request: Request) -> Response:
    form = await request.form()
    upload = form.get("file")
    try:
        if not isinstance(upload, UploadFile):
            raise SkillError("choose a .zip file")
        name = await asyncio.to_thread(market().add_skill_zip, await upload.read())
    except SkillError as exc:
        return render(request, "skills.html", 400, skills=market().list_skills(),
                      ok=False, msg=str(exc))
    return render(request, "skills.html", skills=market().list_skills(), ok=True,
                  msg=f"Skill {name} published. Your clients get it on their next session.")


@protected
async def skills_new(request: Request) -> Response:
    form = await request.form()
    name = field(form, "name")
    if not SKILL_NAME_RE.match(name):
        return render(request, "skills.html", 400, skills=market().list_skills(), ok=False,
                      msg="Skill names use lowercase letters, digits and dashes.")
    return go(f"/setup/skills/{name}")


@protected
async def skill_edit(request: Request) -> Response:
    name = request.path_params["name"]
    if not SKILL_NAME_RE.match(name):
        return go("/setup/skills")
    m = market()
    if request.method == "GET":
        try:
            text, is_new = m.read_skill_md(name), False
        except SkillError:
            text, is_new = NEW_SKILL.format(name=name), True
        return render(request, "skill.html", name=name, text=text, is_new=is_new)
    form = await request.form()
    text = form.get("text", "")
    text = text.replace("\r\n", "\n") if isinstance(text, str) else ""
    try:
        await asyncio.to_thread(m.save_skill_md, name, text, True)
    except SkillError as exc:
        return render(request, "skill.html", 400, name=name, text=text, is_new=False,
                      ok=False, msg=str(exc))
    return go("/setup/skills")


@protected
async def skill_delete(request: Request) -> Response:
    try:
        await asyncio.to_thread(market().delete_skill, request.path_params["name"])
    except SkillError:
        pass
    return go("/setup/skills")


def plugins_url(base_url: str, token: str) -> str:
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, f"cortex:{token}@{parts.netloc}",
                       parts.path.rstrip("/") + "/plugins.git", "", ""))


def client_settings(base_url: str, mcp_token: str, plugins_token: str) -> dict:
    return {
        "env": {"CORTEX_MCP_TOKEN": mcp_token},
        "extraKnownMarketplaces": {"cortex": {
            "source": {"source": "git", "url": plugins_url(base_url, plugins_token)},
            "autoUpdate": True,
        }},
        "enabledPlugins": {"cortex@cortex": True, "my-skills@cortex": True},
    }


@protected
async def connect(request: Request) -> Response:
    cfg = config.load()
    loc = local()
    msg = ok = None
    if request.method == "POST":
        for key in INTERNAL:
            loc.set(key, auth.new_token())
        msg, ok = "Tokens regenerated. Update your clients.", True
    tokens = loc.get_all()
    base_url = cfg["instance"]["base_url"] or "http://localhost:8700"
    mcp_token = tokens.get("CORTEX_MCP_TOKEN", "")
    plugins_token = tokens.get("CORTEX_PLUGINS_TOKEN", "")
    settings = json.dumps(client_settings(base_url, mcp_token, plugins_token), indent=2)
    return render(request, "connect.html", base_url=base_url, mcp_token=mcp_token,
                  plugins_token=plugins_token, settings=settings, msg=msg, ok=ok)


@protected
async def health_page(request: Request) -> Response:
    rows = await asyncio.to_thread(checks.health, config.load())
    return render(request, "health.html", rows=rows)


# --- rutinas ------------------------------------------------------------------------------

def age(ts: str | None) -> str:
    if not ts:
        return "never"
    try:
        seconds = (datetime.now().astimezone() - datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return ts
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{int(seconds // size)} {unit} ago"
    return "just now"


def routine_rows(cfg: dict) -> list[dict]:
    try:
        env = resolve_env(cfg)
    except Exception:
        env = {}
    running = runner.current_run()
    rows = []
    for name in runner.ROUTINES:
        last = routine_report.read_report(name)
        state = runner.scheduled_state(name, cfg, env)
        if running and running.get("routine") == name:
            state = "running"
        rows.append({"name": name, "state": state, "settings": cfg["routines"][name],
                     "model": runner.effective_model(cfg, name) if runner.ROUTINES[name].needs_claude else "",
                     "last": last, "age": age(last["ts"] if last else None)})
    return rows


@protected
async def routines_page(request: Request) -> Response:
    msg = ok = None
    if request.method == "POST":
        form = await request.form()
        url = field(form, "alert_url")
        if url and not url.startswith(("http://", "https://")):
            msg, ok = "The alert URL must start with http:// or https://", False
        else:
            cfg = config.load()
            cfg["alert_url"] = url
            config.save(cfg)
            msg, ok = ("Alerts will be posted there when a routine fails." if url
                       else "Failure alerts are off."), True
    cfg = config.load()
    rows = await asyncio.to_thread(routine_rows, cfg)
    return render(request, "routines.html", 200 if ok is not False else 400, rows=rows,
                  msg=msg, ok=ok)


@protected
async def routine_page(request: Request) -> Response:
    name = request.path_params["name"]
    if name not in runner.ROUTINES:
        return go("/setup/routines")
    msg = ok = None
    status = 200
    if request.method == "POST":
        form = await request.form()
        schedule = field(form, "schedule")
        enabled = form.get("enabled") == "on"
        cfg = config.load()
        settings = cfg["routines"][name]
        # Apagar una rutina con un horario inválido ya guardado no exige corregirlo antes.
        keeps_saved_off = not enabled and schedule == settings["schedule"]
        if not validate_schedule(schedule) and not keeps_saved_off:
            msg, ok, status = f"Not a 5-field cron expression: {schedule!r}", False, 400
        else:
            settings.update(enabled=enabled, schedule=schedule)
            if runner.ROUTINES[name].needs_claude:
                settings["model"] = field(form, "model")
            if name == "clean":
                settings["apply"] = form.get("apply") == "on"
            config.save(cfg)
            write_crontab(cfg)
            msg, ok = "Saved. The scheduler picks it up right away.", True
    cfg = config.load()
    row = next(r for r in await asyncio.to_thread(routine_rows, cfg) if r["name"] == name)
    history = routine_report.read_history(name)
    for item in history:
        item["age"] = age(item.get("ts"))
    return render(request, "routine.html", status, row=row, history=history, msg=msg, ok=ok,
                  needs_claude=runner.ROUTINES[name].needs_claude)


@protected
async def routine_run(request: Request) -> Response:
    name = request.path_params["name"]
    if name in runner.ROUTINES:
        log = paths.logs_dir() / f"routine-{name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as out:
            subprocess.Popen(["cortex-routine", name], stdout=out, stderr=subprocess.STDOUT,
                             start_new_session=True)
    return go(f"/setup/routines/{name}")


@protected
async def clients_page(request: Request) -> Response:
    msg = ok = None
    if request.method == "POST":
        form = await request.form()
        cfg = config.load()
        ip = field(form, "ip")
        if field(form, "action") == "delete":
            cfg["clients"].pop(ip, None)
            config.save(cfg)
            msg, ok = f"{ip} removed.", True
        else:
            name = field(form, "name")
            try:
                ip = str(ipaddress.ip_address(ip))
            except ValueError:
                msg, ok = f"Not an IP address: {ip!r}", False
            else:
                if not name or len(name) > 40:
                    msg, ok = "The name needs 1 to 40 characters.", False
                else:
                    cfg["clients"][ip] = name
                    config.save(cfg)
                    msg, ok = f"{ip} is now shown as {name}.", True
    clients = sorted(config.load()["clients"].items(), key=lambda item: item[1].lower())
    return render(request, "clients.html", 200 if ok is not False else 400, clients=clients,
                  msg=msg, ok=ok)


# --- dominios ------------------------------------------------------------------------------

def domains_context() -> dict:
    from ..domains import cluster, store  # noqa: PLC0415
    vault = paths.vault_dir()
    counts = cluster.domain_counts(vault)
    groups = cluster.groups(vault)
    _, notes = cluster.note_graph(vault)
    raw = [{"id": g.id, "size": len(g.notes), "fits": g.fits, "cohesion": g.cohesion,
            "titles": [notes[s]["meta"].get("title") or s for s in g.notes[:6]]} for g in groups]
    return {"domains": sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])),
            "proposals": store.load(), "groups": raw}


def domains_action(form) -> tuple[str, bool]:
    from ..domains import assign, cluster, store  # noqa: PLC0415
    vault = paths.vault_dir()
    action = field(form, "action")
    if action == "dismiss":
        store.dismiss(field(form, "id"))
        return "Proposal dismissed.", True
    if action in ("rename", "merge"):
        old = field(form, "source")
        new = field(form, "target").lower()
        if not assign.valid_name(new):
            return "Domain names use lowercase letters, digits and dashes (2 to 24), and not «general».", False
        moved = assign.rename(vault, old, new)
        store.forget_domain(old)
        verb = "renamed to" if action == "rename" else "merged into"
        return f"«{old}» {verb} «{new}»: {moved} notes updated.", True
    if action == "name-group":
        name = field(form, "name").lower()
        if not assign.valid_name(name):
            return "Domain names use lowercase letters, digits and dashes (2 to 24), and not «general».", False
        group = next((g for g in cluster.groups(vault) if g.id == field(form, "id")), None)
        if group is None:
            return "That group changed since the page loaded: look again.", False
        _, notes = cluster.note_graph(vault)
        skipped: list[str] = []
        changed = assign.apply(vault, {notes[s]["rel"]: name for s in group.notes},
                               skipped=skipped)
        extra = f" {len(skipped)} without frontmatter were skipped." if skipped else ""
        return f"{len(changed)} notes are now in «{name}».{extra}", True
    return "Unknown action.", False


@protected
async def domains_page(request: Request) -> Response:
    msg = ok = None
    if request.method == "POST":
        msg, ok = await asyncio.to_thread(domains_action, await request.form())
    ctx = await asyncio.to_thread(domains_context)
    return render(request, "domains.html", 200 if ok is not False else 400, msg=msg, ok=ok, **ctx)


def create_app() -> Starlette:
    key = config.load()["session_key"]
    if not key:
        raise RuntimeError("session_key is missing: run cortex-init before the UI")
    routes = [
        Route("/", root),
        Route("/setup", home),
        Route("/setup/claim", claim, methods=["GET", "POST"]),
        Route("/setup/login", login, methods=["GET", "POST"]),
        Route("/setup/logout", logout, methods=["POST"]),
        Route("/setup/wizard/{step:int}", wizard, methods=["GET", "POST"]),
        Route("/setup/secrets", secrets_page, methods=["GET", "POST"]),
        Route("/setup/skills", skills_page),
        Route("/setup/skills/upload", skills_upload, methods=["POST"]),
        Route("/setup/skills/new", skills_new, methods=["POST"]),
        Route("/setup/skills/{name}", skill_edit, methods=["GET", "POST"]),
        Route("/setup/skills/{name}/delete", skill_delete, methods=["POST"]),
        Route("/setup/connect", connect, methods=["GET", "POST"]),
        Route("/setup/health", health_page),
        Route("/setup/routines", routines_page, methods=["GET", "POST"]),
        Route("/setup/routines/{name}", routine_page, methods=["GET", "POST"]),
        Route("/setup/routines/{name}/run", routine_run, methods=["POST"]),
        Route("/setup/clients", clients_page, methods=["GET", "POST"]),
        Route("/setup/domains", domains_page, methods=["GET", "POST"]),
        Route("/auth/session", auth_session),
        Route("/auth/mcp", auth_mcp),
        Route("/plugins.git/{path:path}", plugins_git, methods=["GET", "POST"]),
    ]
    middleware = [Middleware(SessionMiddleware, secret_key=key, session_cookie="cortex_session",
                             same_site="strict", max_age=7 * 24 * 3600)]
    return Starlette(routes=routes, middleware=middleware)
