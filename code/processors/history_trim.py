"""history_trim — cap conversation history to last N turns (system messages exempt)."""

from . import register, TurnContext

MAX_TURNS = 20  # ~10 user+assistant pairs


@register("history_trim")
def history_trim(ctx: TurnContext) -> TurnContext:
    if not ctx.history:
        return ctx
    if len(ctx.history) > MAX_TURNS:
        kept = ctx.history[-MAX_TURNS:]
        ctx.history.clear()
        ctx.history.extend(kept)
        ctx.notes.append(f"history trimmed to last {MAX_TURNS} turns")
    return ctx
