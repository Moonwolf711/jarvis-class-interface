"""persona_inject — make sure ctx.persona is set to the registry's active persona."""

from . import register, TurnContext


@register("persona_inject")
def persona_inject(ctx: TurnContext) -> TurnContext:
    pm = ctx.pipeline_ref
    if pm is None or getattr(pm, "_persona_registry", None) is None:
        return ctx
    active = pm._persona_registry.get()
    if active is not None:
        ctx.persona = active
        ctx.notes.append(f"persona={active.name}")
    return ctx
