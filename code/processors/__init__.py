"""Pluggable processors — Pipecat/Vision-Agents-style composition.

A Processor is a sync callable receiving a TurnContext and mutating it. They
form a chain run before each LLM call. Persona YAMLs declare which processors
to run via the `processors:` list. Unknown names are skipped with a warning.

Stages:
  pre  — run before LLM call (rewrite text, attach images, inject context)
  post — reserved for future use (filter/transform model output)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TurnContext:
    """Mutable state passed through the processor chain for one user turn."""

    user_text: str
    history: List[Dict] = field(default_factory=list)
    persona: Optional[Any] = None
    image_b64: Optional[str] = None
    image_media_type: str = "image/jpeg"
    llm_kwargs: Dict[str, Any] = field(default_factory=dict)
    pipeline_ref: Optional[Any] = None  # ref to SpeechPipelineManager for state access
    notes: List[str] = field(default_factory=list)


_REGISTRY: Dict[str, Callable[[TurnContext], TurnContext]] = {}


def register(name: str):
    def deco(fn: Callable[[TurnContext], TurnContext]):
        _REGISTRY[name] = fn
        logger.debug(f"⚙️ processor registered: {name}")
        return fn
    return deco


def get(name: str) -> Optional[Callable[[TurnContext], TurnContext]]:
    return _REGISTRY.get(name)


def all_names() -> List[str]:
    return sorted(_REGISTRY.keys())


def build_chain(names: List[str]) -> List[Callable[[TurnContext], TurnContext]]:
    chain = []
    for n in names or []:
        fn = _REGISTRY.get(n)
        if fn is None:
            logger.warning(f"⚙️⚠️ unknown processor '{n}' — skipping")
            continue
        chain.append(fn)
    return chain


def apply_chain(chain: List[Callable[[TurnContext], TurnContext]], ctx: TurnContext) -> TurnContext:
    for fn in chain:
        try:
            ctx = fn(ctx) or ctx
        except Exception as exc:
            logger.warning(f"⚙️💥 processor {fn.__name__} raised: {exc}")
    return ctx


# Import side-effect: register built-in processors
from . import vision_attach  # noqa: F401
from . import history_trim   # noqa: F401
from . import clock_inject   # noqa: F401
from . import persona_inject  # noqa: F401
