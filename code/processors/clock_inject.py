"""clock_inject — quietly prepend a UTC timestamp so personas can sense time."""

from datetime import datetime, timezone

from . import register, TurnContext


@register("clock_inject")
def clock_inject(ctx: TurnContext) -> TurnContext:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    # We don't rewrite the user text — instead we extend the system_prompt for THIS call.
    # The LLM module already pulls system_prompt from self.llm.system_prompt; here we
    # leave a note via llm_kwargs that the Anthropic branch can choose to ignore.
    ctx.llm_kwargs.setdefault("clock_hint", now)
    return ctx
