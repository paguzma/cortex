"""Paso de dominios del consolidador: nombra los grupos nuevos y deja propuestas."""
from ..routines.base import Context
from . import assign, cluster, naming, store

MAX_DOMAINS = 7
TIMEOUT = 600


def proposals_for(before: dict[str, int], after: dict[str, int]) -> list[dict]:
    out = [{"id": f"new:{d}", "kind": "new", "domain": d, "notes": after[d],
            "text": f"New domain «{d}» ({after[d]} notes): rename or merge it in Domains"}
           for d in sorted(set(after) - set(before))]
    if len(after) > MAX_DOMAINS:
        source, target = sorted(after, key=lambda d: (after[d], d))[:2]
        out.append({"id": f"merge:{source}:{target}", "kind": "merge", "source": source,
                    "target": target,
                    "text": f"{len(after)} domains: consider merging «{source}» into «{target}»"})
    return out


def run(ctx: Context) -> tuple[str, str]:
    groups = cluster.groups(ctx.vault)
    if not groups:
        return "domains: nothing new", ""
    _, notes = cluster.note_graph(ctx.vault)
    before = dict(cluster.domain_counts(ctx.vault))
    cwd = ctx.state / "cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    rc, output = ctx.claude(naming.prompt(groups, notes, before), model=ctx.model, env=ctx.env,
                            cwd=cwd, timeout=TIMEOUT)
    if rc != 0:
        return f"domains: the model failed (exit {rc})", output[-4000:]
    decisions = naming.parse(output, {g.id for g in groups})
    if not decisions:
        return "domains: the model named no group", output[-4000:]
    assignments = {notes[stem]["rel"]: decisions[g.id]
                   for g in groups if g.id in decisions for stem in g.notes}
    skipped: list[str] = []
    changed = assign.apply(ctx.vault, assignments, skipped=skipped)
    after = dict(cluster.domain_counts(ctx.vault))
    added = store.add(proposals_for(before, after))
    new = sorted(set(after) - set(before))
    detail = "\n".join([f"{rel} → {assignments[rel]}" for rel in changed]
                       + [f"{rel}: skipped, no frontmatter" for rel in skipped])
    return (f"domains: {len(changed)} notes named · new: {', '.join(new) or 'none'}"
            + (f" · {added} proposals" if added else "")
            + (f" · {len(skipped)} skipped" if skipped else ""), detail)
