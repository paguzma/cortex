"""Lo que espera al dueño de la instancia: la caja «Pending» del panel.

Funciones puras; el recolector hace la E/S. Cinco fuentes:
  - observaciones `- [pendiente]` / `- [pending]` vigentes (sin tachar) del vault
  - notas por revisar: hechos `(as of …)` de 6 meses o más, o una nota que pasó el
    plazo de su tipo sin tocarse (`updated:`). Revisarla es actualizar esa fecha.
  - rutinas cuyo último reporte salió `fail`
  - el token de Claude a punto de vencer (dura un año desde que se cargó)
  - propuestas de dominios que dejó el consolidador (nuevos y fusiones sugeridas)
"""
from __future__ import annotations

import re
from datetime import date, timedelta

RX_PENDING = re.compile(r"^- \[(?:pendiente|pending)\]\s*(.+)$", re.I)
RX_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
SCOPE = ("entities/", "references/", "decisions/")
TOKEN_LIFETIME_DAYS = 365
RX_AS_OF = re.compile(r"\(as of (\d{4}-\d{2})(?:-\d{2})?\)")
RX_STRUCK = re.compile(r"~~.*?~~")
AS_OF_MONTHS = 6
# Cada cuánto conviene mirar una nota según su tipo. Sin plazo: personas, referencias y
# episodios (append-only). Son avisos para revisar, no vencimientos.
REVIEW_DAYS = {"project": 90, "infra": 180, "tool": 180, "feedback": 365, "decision": 365}
TOKEN_WARNING_DAYS = 30


def vault_pending(texts: dict[str, tuple[str, str | None]]) -> list[dict]:
    """texts = {ruta relativa: (texto, updated)} -> observaciones pendientes vigentes."""
    out = []
    for rel, (text, updated) in texts.items():
        if not rel.startswith(SCOPE):
            continue
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ~~"):
                continue
            m = RX_PENDING.match(stripped)
            if not m:
                continue
            body = m.group(1).strip()
            found = RX_DATE.search(body)
            out.append({"kind": "vault", "text": body, "path": rel,
                        "date": found.group(1) if found else (str(updated)[:10] if updated else None)})
    return out


def failed_routines(reports: dict[str, dict | None]) -> list[dict]:
    out = []
    for name, rep in sorted(reports.items()):
        if rep and rep.get("status") == "fail":
            out.append({"kind": "routine", "text": f"{name} failed: {rep.get('summary') or 'no summary'}",
                        "routine": name, "date": str(rep.get("ts") or "")[:10] or None})
    return out


def token_expiry(cfg: dict, today: date) -> list[dict]:
    """El token de Claude vence a los 365 días de cargado: avisa 30 días antes (o ya vencido)."""
    loaded = (cfg.get("claude") or {}).get("token_loaded_at")
    if not loaded:
        return []
    try:
        loaded_on = date.fromisoformat(str(loaded)[:10])
    except ValueError:
        return []
    expires = loaded_on + timedelta(days=TOKEN_LIFETIME_DAYS)
    if expires > today + timedelta(days=TOKEN_WARNING_DAYS):
        return []
    left = (expires - today).days
    text = (f"the Claude token expired on {expires.isoformat()}" if left < 0
            else f"the Claude token expires in {left} days ({expires.isoformat()})")
    return [{"kind": "token", "text": text + " — load a new one in setup", "date": loaded_on.isoformat()}]


def needs_review(texts: dict[str, tuple[str, str | None, str | None]], today: date) -> list[dict]:
    """texts = {ruta: (texto, updated, type)} -> una entrada por nota que conviene revisar."""
    cutoff = (today.year * 12 + today.month - 1) - AS_OF_MONTHS
    out = []
    for rel, (text, updated, kind) in sorted(texts.items()):
        if rel.startswith("episodes/"):
            continue
        old = sorted(m for line in text.split("\n")
                     for m in RX_AS_OF.findall(RX_STRUCK.sub("", line))
                     if int(m[:4]) * 12 + int(m[5:7]) - 1 <= cutoff)
        reasons, when = [], None
        if old:
            reasons.append(f"{len(old)} fact{'s' if len(old) > 1 else ''} «as of» {AS_OF_MONTHS}+ months ago")
            when = old[0]
        days = REVIEW_DAYS.get(kind or "")
        if days and updated:
            try:
                touched = date.fromisoformat(str(updated)[:10])
            except ValueError:
                touched = None
            if touched and (today - touched).days > days:
                reasons.append(f"not updated since {touched.isoformat()} ({kind}: every {days} days)")
                when = min(filter(None, (when, touched.isoformat()[:7])))
        if reasons:
            out.append({"kind": "review", "text": f"still true? {'; '.join(reasons)}",
                        "path": rel, "date": when})
    return out


def sort_items(items: list[dict]) -> list[dict]:
    """Del más viejo al más nuevo; los sin fecha al final. Los «por revisar» van después de
    todo lo demás: suelen ser viejos y taparían lo que de verdad espera al dueño."""
    return sorted(items, key=lambda i: (i.get("kind") == "review", i.get("date") is None, i.get("date") or "",
                                        i.get("kind", ""), i.get("text", "")))


KINDS = ("vault", "routine", "token", "review")


def collect(texts: dict[str, tuple[str, str | None]], reports: dict[str, dict | None],
            cfg: dict, today: date, proposals: list[dict] | None = None,
            review: dict[str, tuple[str, str | None, str | None]] | None = None) -> dict:
    """Cada fuente por separado: si una falla, las otras siguen y la falla se nombra."""
    items, failures = [], []
    for name, fn in (("vault", lambda: vault_pending(texts)),
                     ("routine", lambda: failed_routines(reports)),
                     ("token", lambda: token_expiry(cfg, today)),
                     ("review", lambda: needs_review(review or {}, today))):
        try:
            items += fn()
        except Exception:  # noqa: BLE001 — una fuente caída no tumba la caja
            failures.append(name)
    items = sort_items(items)
    counts = {k: (None if k in failures else sum(1 for i in items if i["kind"] == k)) for k in KINDS}
    domain_proposals = [{"id": p["id"], "kind": p.get("kind"), "text": p.get("text", p["id"])}
                        for p in proposals or []]
    return {"items": items, "counts": counts, "failures": failures,
            "domain_proposals": domain_proposals}
