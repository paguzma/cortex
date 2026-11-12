from cortex.routines import consolidate
from cortex.seed import seed_vault


def fake_claude(rc=0, output="Fusioné 0 notas.", effect=None):
    calls = []

    def claude(prompt, **kwargs):
        calls.append((prompt, kwargs))
        if effect:
            effect()
        return rc, output
    return claude, calls


def seeded(ctx):
    import shutil
    shutil.rmtree(ctx.vault)
    seed_vault(ctx.vault, "2026-10-08")
    return ctx


def test_prompt_carries_validator_output(ctx):
    seeded(ctx)
    text = consolidate.prompt(ctx)
    assert "STRUCTURE OK" in text and "{{STRUCTURE}}" not in text


def test_agent_failure_is_fail(ctx):
    seeded(ctx)
    ctx.claude, _ = fake_claude(rc=1, output="boom")
    result = consolidate.run(ctx)
    assert result.status == "fail" and "exit 1" in result.summary


def test_empty_output_is_fail(ctx):
    seeded(ctx)
    ctx.claude, _ = fake_claude(output="  ")
    assert consolidate.run(ctx).status == "fail"


def test_healthy_vault_is_ok_and_uses_tools_and_mcp(ctx):
    seeded(ctx)
    ctx.claude, calls = fake_claude()
    result = consolidate.run(ctx)
    assert result.status == "ok" and result.summary.startswith("vault healthy · 0 notes")
    kwargs = calls[0][1]
    assert kwargs["mcp"] is True and "mcp__cortex" in kwargs["allowed_tools"]
    assert "Bash" not in kwargs["allowed_tools"] and kwargs["model"] == "opus"


def test_agent_that_breaks_the_vault_is_fail(ctx):
    seeded(ctx)
    def damage():
        (ctx.vault / "INDEX.md").unlink()
    ctx.claude, _ = fake_claude(output="Todo perfecto.", effect=damage)
    result = consolidate.run(ctx)
    assert result.status == "fail" and result.summary.startswith("REVIEW: STRUCTURE")
    assert "INDEX.md" in result.detail


def test_prompt_caps_the_violation_list(ctx):
    seeded(ctx)
    for i in range(consolidate.MAX_VIOLATIONS + 50):
        (ctx.vault / "feedback" / f"suelta-{i}.md").write_text("sin frontmatter\n")
    text = consolidate.prompt(ctx)
    assert text.count("suelta-") <= consolidate.MAX_VIOLATIONS
    assert "more violations not shown" in text
    assert len(text.encode()) < 128 * 1024
