"""clean.py — la rutina clean: cada noche, hasta 2 notas del vault y su vecindario.

1. elegir (script) · 2. sacar lo tachado al historial (script) · 3. ordenar la nota
(modelo) · 4. tachar en las vecinas lo que contradice (modelo) · 5. validar (script)
y aplicar. Sin `routines.clean.apply` solo PROPONE: corre todo y devuelve el diff.

Las notas se escriben atómicamente directo al disco: basic-memory sincroniza el
archivo y conserva su nombre aunque cambie el título (con `write_note` el archivo
se renombraría).

Dónde vive cada cosa (todo sale del Context):
- vault: ctx.vault (repo git).
- historial: ctx.archive, que ES el repo git; lo tachado de una nota va a
  ctx.archive / <ruta relativa de la nota>.
- propuestas, registro y backoff: bajo ctx.state; el cwd del modelo, ctx.state / "cwd".

El runner del producto toma un flock global: dos rutinas nunca tocan el vault a la vez."""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import traceback
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from ...gitutil import git, init_repo
from .. import structure
from ..base import Context, Result
from . import model, notes, pick, split, validate

MAX_NOTES = 10        # válvula: notas distintas que se pueden tocar en una noche
MAX_TARGETS = 2
MAX_NEIGHBORS = 8     # llamadas al modelo por vecinas de un objetivo: un hub no puede costar una por vecina
PROPOSAL_DAYS = 14    # carpetas de propuestas completas que se conservan
MAX_DIFF = 4000       # caracteres de diff por nota en el detalle (el detalle se corta a 20 KB)
COMMIT_PREFIX = "chore(clean):"
# Los commits de esta rutina no son «cambios de estado»: si contaran, la nota limpiada
# anoche (y la vecina recién tachada) volverían a elegirse cada noche.
NOT_OURS = ("--invert-grep", "--grep=^chore(clean):")


def apply_enabled(cfg: dict) -> bool:
    """Solo un True explícito aplica; cualquier otra cosa (o la falta de la clave) propone."""
    try:
        return cfg["routines"]["clean"]["apply"] is True
    except (KeyError, TypeError):
        return False


def write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def diff(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"a/{path}", f"b/{path}", n=1))


def touched_count(results: list[dict]) -> int:
    """Notas distintas que se escribirían en el vault: objetivos y vecinas aprobados."""
    return len({r["path"] for r in results if r["verdict"].ok}
               | {v["path"] for r in results for v in r["neighbors"] if v["verdict"].ok})


def _read(p: Path) -> str | None:
    return p.read_text(encoding="utf-8") if p.exists() else None


# «XY ruta» de `git status --porcelain`. gitutil.git recorta la salida, así que la primera
# línea puede perder el espacio inicial de XY: por eso se parsea con regex y no con l[3:].
_RX_PORCELAIN = re.compile(r"^[ MTADRCU?!]{1,2} (.*)$")


def dirty(repo: Path, *paths: str) -> list[str]:
    """Rutas con cambios sin commitear (de todo el repo si no se dan rutas), sin agrupar carpetas."""
    out = git(repo, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    found = []
    for line in out.splitlines():
        if not line.strip():
            continue
        m = _RX_PORCELAIN.match(line)
        found.append(m.group(1) if m else line.strip())
    return found


def _head(repo: Path) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


class Terminated(Exception):
    """SIGTERM (el runner parando la rutina, un timeout) convertido en excepción: así corre la restauración."""


def _on_sigterm(signum, frame):
    raise Terminated("received SIGTERM")


def _main_thread() -> bool:
    # signal.signal solo se puede llamar desde el hilo principal.
    return threading.current_thread() is threading.main_thread()


@contextmanager
def no_sigterm():
    """Mientras se restaura o se revierte, un SIGTERM no puede cortar a la mitad."""
    if not _main_thread():
        yield
        return
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextmanager
def sigterm_raises():
    """Durante toda la corrida, SIGTERM levanta Terminated (y así se deshace lo que iba a medias)."""
    if not _main_thread():
        yield
        return
    previous = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


class Cleaner:
    def __init__(self, ctx: Context, today: str | None = None, max_notes: int = MAX_NOTES):
        self.ctx = ctx
        self.vault = Path(ctx.vault)
        self.archive = Path(ctx.archive)
        self.state = Path(ctx.state)
        self.today = today or date.today().isoformat()
        self.max_notes = max_notes
        self.apply = apply_enabled(ctx.cfg)

    # --- lectura ---

    def scope_texts(self) -> dict[str, str]:
        out = {}
        for base in notes.SCOPE:
            for f in (self.vault / base).rglob("*.md"):
                out[f.relative_to(self.vault).as_posix()] = f.read_text(encoding="utf-8")
        return out

    def changed_24h(self) -> set[str]:
        """Notas que cambiaron de estado en 24 h, sin contar los commits de esta rutina. Las
        creadas en ese lapso no cuentan: su `status:` aparece como agregado en el diff y le
        quitarían el cupo a una que sí lo necesita."""
        created = set(git(self.vault, "log", "--since=24.hours", *NOT_OURS, "--diff-filter=A",
                          "--format=", "--name-only").splitlines())
        paths = {l for l in git(self.vault, "log", "--since=24.hours", *NOT_OURS, "--format=",
                                "--name-only").splitlines()
                 if notes.in_scope(l) and l not in created}
        return {p for p in paths
                if pick.state_changed(git(self.vault, "log", "--since=24.hours", *NOT_OURS, "-p",
                                          "--format=", "--", p))}

    def problem_count(self) -> int:
        """Violaciones de estructura + problemas del grafo, según el validador del producto."""
        return len(structure.check(self.vault))

    # --- propuestas ---

    def proposals_dir(self) -> Path:
        return self.state / "proposals" / self.today

    def proposal_file(self, path: str, ext: str) -> Path:
        """entities/infra/pve.md → <state>/proposals/<fecha>/entities__infra__pve.<ext>"""
        return self.proposals_dir() / f"{path.removesuffix('.md').replace('/', '__')}.{ext}"

    def bounded_diff(self, old: str, new: str, path: str) -> str:
        """El diff de una nota, cortado a MAX_DIFF: una nota grande no puede comerse el resto del detalle.
        El completo queda en el .diff de la propuesta."""
        d = diff(old, new, path)
        if len(d) <= MAX_DIFF:
            return d
        cut = d.rfind("\n", 0, MAX_DIFF) + 1 or MAX_DIFF
        return d[:cut] + (f"[diff truncated: {len(d) - cut} more characters; "
                          f"full diff in {self.proposal_file(path, 'diff')}]")

    def save_proposals(self, results: list[dict], texts: dict[str, str]) -> None:
        """Texto candidato y diff completo de cada objetivo y vecina tocada (las dos modalidades), y poda
        a las últimas PROPOSAL_DAYS fechas. De una vecina va su último texto (lleva todos los tachados)."""
        pending: dict[str, tuple[str, str]] = {}
        for r in results:
            pending[r["path"]] = (r["original"], r["candidate"])
            for v in r["neighbors"]:
                pending[v["path"]] = (texts[v["path"]], v["new"])
        self.proposals_dir().mkdir(parents=True, exist_ok=True)
        for path, (old, new) in pending.items():
            write_atomic(self.proposal_file(path, "md"), new)
            write_atomic(self.proposal_file(path, "diff"), diff(old, new, path))
        dates = sorted(d for d in (self.state / "proposals").iterdir() if d.is_dir())
        for old in dates[:-PROPOSAL_DAYS]:
            shutil.rmtree(old, ignore_errors=True)

    # --- pasos 2 a 5 ---

    def process(self, path: str, reason: str, texts: dict[str, str], targets: set[str],
                pending: dict[str, str]) -> dict:
        """Pasos 2-5 (sin escribir) para un objetivo. Devuelve todo lo que hace falta para aplicar y reportar.

        `targets`: las notas que se limpian esta noche; ninguna es vecina de otra (se reescriben enteras).
        `pending`: texto ya tachado (y aprobado) de cada vecina por un objetivo anterior. Una vecina de
        los dos objetivos se tacha sobre ese texto, así quedan los dos tachados y cada uno se valida
        contra el texto que tenía delante. Se modifica en el lugar: quien llama pasa una copia y la
        adopta solo si este objetivo termina sin excepción."""
        original = texts[path]
        hist_path = self.archive / path
        base, moved = split.separate(original, str(hist_path))
        output = model.call(self.ctx, model.prompt_tidy(base, self.today, moved))
        candidate = model.extract(output, "nota")
        if candidate is None:
            verdict = validate.Verdict()
            verdict.error("the model did not return <nota>")
            candidate = base
        else:
            candidate = candidate.rstrip("\n") + "\n"
            verdict = validate.validate_target(base, candidate)
        hist_old = hist_path.read_text(encoding="utf-8") if hist_path.exists() else None
        hist_new = split.append_history(hist_old, path, self.today, moved) if moved else hist_old
        if moved:
            vh = validate.validate_history(hist_old, hist_new)
            if not vh.ok:
                verdict.ok = False
                verdict.errors += vh.errors
        slug = Path(path).stem
        current = candidate if verdict.ok else base
        target_facts = frozenset(notes.facts(current))
        neighbors = []
        candidates = [nb for nb in notes.linking_to(texts, path) if nb not in targets]
        for nb in candidates[:MAX_NEIGHBORS]:
            before = pending.get(nb, texts[nb])
            raw = model.call(self.ctx, model.prompt_neighbors(slug, current, nb, before))
            try:
                items = json.loads(model.extract(raw, "json") or "[]")
            except ValueError:
                items = []
            if not isinstance(items, list):
                items = []
            new, applied, ignored = model.strike(before, [i for i in items if isinstance(i, dict)],
                                                 self.today, slug)
            if not applied:
                continue
            vv = validate.validate_neighbor(before, new, slug, target_facts=target_facts)
            if vv.ok:
                pending[nb] = new
            neighbors.append({"path": nb, "new": new, "verdict": vv, "ignored": ignored})
        return {"path": path, "reason": reason, "original": original, "base": base,
                "candidate": candidate, "verdict": verdict, "moved": moved, "hist_old": hist_old,
                "hist_new": hist_new, "neighbors": neighbors,
                "unchecked": candidates[MAX_NEIGHBORS:]}

    def detail_of(self, results: list[dict], postponed: list[dict] = (), notices: list[str] = ()) -> str:
        mode = "APPLY" if self.apply else "PROPOSE ONLY (routines.clean.apply is off)"
        parts = [f"Mode: {mode} · model {self.ctx.model}\n"]
        parts.append(f"Full proposals: {self.proposals_dir()}")
        parts += list(notices)
        parts += [f"postponed by the valve: {r['path']} — {r['reason']} "
                  f"(with its neighborhood they added up to more than {self.max_notes} notes; "
                  f"it comes back another night)" for r in postponed]
        for r in results:
            v = r["verdict"]
            parts.append(f"=== {r['path']} — {r['reason']}")
            parts.append(f"{len(r['moved'])} struck lines moved to the history · "
                         f"{len(r['original'].encode())} → {len(r['candidate'].encode())} bytes")
            parts += [f"  · {c}" for c in v.changes]
            if v.ok:
                parts.append("validator: OK")
                parts.append(self.bounded_diff(r["original"], r["candidate"], r["path"]))
            else:
                parts += ["validator: REJECTED, not applied"] + [f"  ✗ {e}" for e in v.errors]
            if r.get("unchecked"):
                parts.append(f"{len(r['unchecked'])} neighbors not checked (max {MAX_NEIGHBORS} "
                             f"per note): {', '.join(r['unchecked'])}")
            for nv in r["neighbors"]:
                parts.append(f"--- neighbor {nv['path']}: {'OK' if nv['verdict'].ok else 'REJECTED'}")
                parts += [f"  · {c}" for c in nv["verdict"].changes]
                parts += [f"  ✗ {e}" for e in nv["verdict"].errors]
                parts += [f"  (ignored, not an exact line of the note: {i})" for i in nv["ignored"]]
        return "\n".join(parts)

    # --- aplicar ---

    def plan_of(self, results: list[dict], texts: dict[str, str]) -> list[dict]:
        """Qué se escribe: {repo, path, before (None si no existe), after}. Historial primero.
        De una vecina tachada por los dos objetivos va solo el último texto aprobado (lleva ambos)."""
        hist, targets, neighbors = [], [], {}
        for r in results:
            if r["verdict"].ok:
                if r["moved"]:
                    hist.append({"repo": self.archive, "path": r["path"], "before": r["hist_old"],
                                 "after": r["hist_new"]})
                targets.append({"repo": self.vault, "path": r["path"], "before": r["original"],
                                "after": r["candidate"]})
            for v in r["neighbors"]:
                if v["verdict"].ok:
                    neighbors[v["path"]] = {"repo": self.vault, "path": v["path"],
                                            "before": texts[v["path"]], "after": v["new"]}
        return hist + targets + list(neighbors.values())

    def changed_path(self, plan: list[dict]) -> str | None:
        """La primera ruta del plan que ya no está como al empezar o tiene cambios sin commitear."""
        for w in plan:
            if (_read(w["repo"] / w["path"]) != w["before"]
                    or git(w["repo"], "status", "--porcelain", "--", w["path"]).strip()):
                return w["path"]
        return None

    def _undo(self, written: list[dict], hist_prev: str | None, hist_committed: bool) -> None:
        """Mejor esfuerzo, antes del commit del vault: vuelve cada archivo a su texto previo, lo saca
        del índice y deshace el commit del historial si lo hizo esta corrida."""
        if hist_committed:
            if hist_prev:   # --keep: como --hard, pero no pisa cambios ajenos en otros archivos
                subprocess.run(["git", "-C", str(self.archive), "reset", "-q", "--keep", hist_prev],
                               capture_output=True)
            else:           # era el primer commit del repo
                subprocess.run(["git", "-C", str(self.archive), "update-ref", "-d", "HEAD"],
                               capture_output=True)
        for w in reversed(written):
            p = w["repo"] / w["path"]
            try:
                if w["before"] is None:
                    p.unlink(missing_ok=True)
                else:
                    write_atomic(p, w["before"])
            except OSError:
                pass
        for repo in (self.vault, self.archive):
            paths = [w["path"] for w in written if w["repo"] == repo]
            if not paths:
                continue
            if _head(repo):
                subprocess.run(["git", "-C", str(repo), "reset", "-q", "--", *paths], capture_output=True)
            else:
                subprocess.run(["git", "-C", str(repo), "rm", "-q", "--cached", "--ignore-unmatch", "--",
                                *paths], capture_output=True)

    def _revert(self, vault_commit: str, hist_commit: str | None, summary: str) -> str:
        """Revierte los commits exactos de esta corrida (puede haber otros encima). Devuelve el resumen de la falla."""
        try:
            git(self.vault, "revert", "--no-commit", vault_commit)
            git(self.vault, "commit", "-q", "-m", f"{COMMIT_PREFIX} revert — {summary}")
        except BaseException as exc:
            subprocess.run(["git", "-C", str(self.vault), "revert", "--abort"], capture_output=True)
            return f"could not revert ({str(exc)[:100]}): the night stayed committed: review by hand"
        if hist_commit:
            try:
                git(self.archive, "revert", "--no-edit", hist_commit)
            except BaseException as exc:
                subprocess.run(["git", "-C", str(self.archive), "revert", "--abort"], capture_output=True)
                return f"vault reverted but the history was not ({str(exc)[:100]}): review by hand"
        return summary

    def apply_changes(self, plan: list[dict], summary: str) -> Result | None:
        """Escribe y commitea: historial → commit → vault → commit → chequeo → revert de ambos si empeoró.
        Devuelve un Result si no quedó aplicado; None si quedó. Si algo explota antes del commit
        del vault, deja todo como estaba y relanza."""
        path = self.changed_path(plan)
        if path:
            return Result("ok", f"skipped: {path} changed during the run or has uncommitted changes")
        baseline = self.problem_count()   # justo antes de escribir: lo ajeno de antes no cuenta
        hist = [w for w in plan if w["repo"] == self.archive]
        targets = [w for w in plan if w["repo"] == self.vault]
        written: list[dict] = []
        hist_prev, hist_committed, hist_commit = _head(self.archive), False, None
        try:
            for w in hist:
                written.append(w)
                write_atomic(self.archive / w["path"], w["after"])
            if hist:
                paths = [w["path"] for w in hist]
                git(self.archive, "add", "--", *paths)
                git(self.archive, "commit", "-q", "-m", f"{COMMIT_PREFIX} {self.today}: {', '.join(paths)}",
                    "--", *paths)
                hist_committed, hist_commit = True, _head(self.archive)
            for w in targets:
                written.append(w)
                write_atomic(self.vault / w["path"], w["after"])
            paths = [w["path"] for w in targets]
            git(self.vault, "add", "--", *paths)
            git(self.vault, "commit", "-q", "-m", f"{COMMIT_PREFIX} {summary}", "--", *paths)
            vault_commit = _head(self.vault)
        except BaseException:
            with no_sigterm():
                self._undo(written, hist_prev, hist_committed)
            raise
        try:
            worse = self.problem_count() > baseline
        except Exception as exc:
            raise RuntimeError(f"the post-apply check failed ({exc}): the night stayed committed: "
                               f"review by hand") from exc
        if worse:
            with no_sigterm():
                return Result("fail", self._revert(
                    vault_commit, hist_commit,
                    f"reverted: the vault ended up with more problems than the baseline ({baseline})"))
        return None

    # --- la corrida ---

    def run(self) -> Result:
        if not (self.archive / ".git").exists():   # el producto lo crea en el primer uso
            init_repo(self.archive)
        dirty_hist = dirty(self.archive)   # nunca se arregla solo: falla, y antes de gastar modelo
        if dirty_hist:
            return Result("fail", f"the history has uncommitted changes: {dirty_hist[0]}",
                          "Uncommitted changes in the history:\n" + "\n".join(dirty_hist)
                          + "\nThe model was not called.")
        texts = self.scope_texts()
        registry_path = self.state / ("registry.json" if self.apply else "registry-proposed.json")
        registry = read_json(registry_path)
        rejections = registry.pop("rejections", None)
        rejections = rejections if isinstance(rejections, dict) else {}
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        def save_registry() -> None:
            self.state.mkdir(parents=True, exist_ok=True)
            write_atomic(registry_path, json.dumps({**registry, "rejections": rejections},
                                                   ensure_ascii=False, indent=1))

        candidates = pick.pick({p: pick.summarize(t) for p, t in texts.items()}, self.changed_24h(),
                               registry, limit=len(texts), rejections=rejections)
        # Un objetivo con cambios sin commitear en el vault cede su lugar al siguiente: se detecta
        # aquí, antes de gastar modelo (la verificación antes de escribir queda como segunda guardia).
        targets, notices = [], []
        for p, m in candidates:
            if len(targets) == MAX_TARGETS:
                break
            if dirty(self.vault, p):
                notices.append(f"skipped: {p} has uncommitted changes in the vault; it comes back another night")
            else:
                targets.append((p, m))
        if not targets:
            extra = f" ({len(notices)} with uncommitted changes)" if notices else ""
            return Result("ok", f"nothing to clean{extra}",
                          "\n".join(notices + ["No other note changed state or is in the disorder queue."]))
        target_paths, pending = {p for p, _ in targets}, {}
        # Un objetivo con el que el modelo falla (timeout, error) va al backoff: si no, vuelve
        # primero cada noche. Lo ya aprobado de los demás objetivos sigue adelante. Cada objetivo
        # tacha sobre una copia de `pending`: si falla a medias, sus tachados no llegan al siguiente.
        results, failed = [], []
        for p, m in targets:
            local = dict(pending)
            try:
                results.append(self.process(p, m, texts, target_paths, local))
                pending = local
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                failed.append(p)
                rejections[p] = {"ts": now, "bytes": len(texts[p].encode()),
                                 "reason": f"model failed: {str(exc)[:200]}"}
                notices.append(f"skipped: the model failed on {p} ({str(exc)[:120]}); "
                               f"it waits before coming back")
                last_error = exc
        if not results:
            save_registry()
            return Result("fail", f"the model failed: {str(last_error)[:120]}", str(last_error))
        # Válvula: si los objetivos con su vecindario pasan de max_notes, se queda solo el primero
        # (ya procesado: no hay otra llamada al modelo) y los demás vuelven otra noche. Como cada
        # objetivo tacha sobre lo que dejó el anterior, quitar los últimos no rompe al primero.
        self.save_proposals(results, texts)
        postponed: list[dict] = []
        if touched_count(results) > self.max_notes and len(results) > 1:
            results, postponed = results[:1], results[1:]
        if not self.apply:   # en propuesta, un pospuesto también espera: si no, se re-propone cada noche
            for r in postponed:
                rejections[r["path"]] = {"ts": now, "bytes": len(r["original"].encode()),
                                         "reason": f"valve: postponed (more than {self.max_notes} "
                                                   f"notes with the other one)"}
        for r in results:   # backoff: un rechazo no se repite cada noche
            if r["verdict"].ok:
                rejections.pop(r["path"], None)
            else:
                rejections[r["path"]] = {"ts": now, "bytes": len(r["original"].encode()),
                                         "reason": "; ".join(r["verdict"].errors)[:300]}
        save_registry()   # los rechazos quedan guardados salga por donde salga
        detail = self.detail_of(results, postponed, notices)
        total = touched_count(results)
        if total > self.max_notes:
            r = results[0]
            rejections[r["path"]] = {"ts": now, "bytes": len(r["original"].encode()),
                                     "reason": f"valve: {total} notes to touch (max {self.max_notes})"}
            save_registry()
            if self.apply:
                return Result("fail", f"valve: {total} notes to touch (max {self.max_notes}); "
                                      f"nothing applied", detail)
            # en modo proponer no se iba a aplicar nada: no es una falla, solo se avisa en el panel
            return Result("ok", f"proposed 0 notes · valve: would have stopped ({total} notes)",
                          f"Max {self.max_notes} notes per night.\n\n{detail}")
        ok_targets = [r for r in results if r["verdict"].ok]
        neighbors_ok = {v["path"] for r in results for v in r["neighbors"] if v["verdict"].ok}
        rejected = sum(1 for r in results if not r["verdict"].ok) + \
            sum(1 for r in results for v in r["neighbors"] if not v["verdict"].ok)
        kb = lambda key: sum(len(r[key].encode()) for r in ok_targets) // 1024  # noqa: E731
        verb = "applied" if self.apply else "proposed"
        summary = (f"{verb} {len(ok_targets)} notes ({kb('original')} KB → {kb('candidate')} KB) · "
                   f"{len(neighbors_ok)} neighbors struck · {rejected} rejected"
                   + (f" · model failed on {len(failed)}" if failed else ""))
        if self.apply and (ok_targets or neighbors_ok):
            try:
                failure = self.apply_changes(self.plan_of(results, texts), summary)
            except Exception as exc:  # git, disco, SIGTERM: igual hay que devolver un Result
                return Result("fail", f"failed to apply: {str(exc)[:160]}",
                              f"{type(exc).__name__}: {exc}\n\n{detail}")
            if failure:
                if failure.status == "fail":   # se revirtió: al backoff, para no reintentarla cada noche
                    for r in ok_targets:
                        rejections[r["path"]] = {"ts": now, "bytes": len(r["original"].encode()),
                                                 "reason": "reverted: the vault got worse"}
                    save_registry()   # solo cambian los rechazos; la parte de notas limpiadas no
                return Result(failure.status, failure.summary, detail)
        for r in ok_targets:   # la nota como queda en disco: la limpia al aplicar, la original al proponer
            registry[r["path"]] = {"ts": now, **pick.summarize(r["candidate"] if self.apply else r["original"])}
        save_registry()
        return Result("ok", summary, detail)


def run(ctx: Context, *, today: str | None = None, max_notes: int = MAX_NOTES) -> Result:
    """Punto de entrada de la rutina. `today` y `max_notes` solo se cambian en pruebas."""
    with sigterm_raises():
        try:
            return Cleaner(ctx, today=today, max_notes=max_notes).run()
        except Exception as exc:   # lo que sea: igual hay que devolver un Result
            return Result("fail", f"unexpected error: {type(exc).__name__}: {str(exc)[:120]}",
                          traceback.format_exc())
