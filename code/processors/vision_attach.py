"""vision_attach — if vision is active and a frame is buffered, attach it."""

from . import register, TurnContext


@register("vision_attach")
def vision_attach(ctx: TurnContext) -> TurnContext:
    pm = ctx.pipeline_ref
    if pm is None:
        return ctx
    if not getattr(pm, "vision_active", False):
        return ctx
    b64 = getattr(pm, "last_frame_b64", None)
    if not b64:
        return ctx
    ctx.image_b64 = b64
    ctx.llm_kwargs["image_b64"] = b64
    ctx.llm_kwargs["image_media_type"] = ctx.image_media_type
    # One-shot — clear buffer so we don't re-send the stale frame
    pm.last_frame_b64 = None
    ctx.notes.append(f"vision frame attached ({len(b64)} chars)")
    return ctx
