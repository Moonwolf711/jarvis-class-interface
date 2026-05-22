"""Persona Registry — loads persona definitions from /app/code/personas/*.yaml.

A persona bundles together: a voice (ElevenLabs ID), an LLM model, a system prompt,
a ring color for the Jarvis orb, and an order/default flag for the UI picker.

The registry is consulted by the SpeechPipelineManager at the start of each
generation — voice ID and system prompt are pulled from whatever persona is active.

Borrowed concept: TEN Framework's property.json-as-config and Vision-Agents'
agent composition model. Code is original.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError as exc:
    raise ImportError(
        "PyYAML required for persona registry — add `pyyaml` to requirements.txt"
    ) from exc

logger = logging.getLogger(__name__)

PERSONAS_DIR = Path(__file__).parent / "personas"


@dataclass
class Persona:
    """One persona — voice + brain + visual identity."""

    name: str
    title: str
    voice_id: str
    voice_name: str
    model: str
    ring_color: str
    system_prompt: str
    order: int = 99
    default: bool = False
    voice_needs_clone: bool = False
    first_sentence_max_words: Optional[int] = None
    extra: Dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.name.lower()

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "voice_id": self.voice_id,
            "voice_name": self.voice_name,
            "voice_needs_clone": self.voice_needs_clone,
            "model": self.model,
            "ring_color": self.ring_color,
            "order": self.order,
            "default": self.default,
            "first_sentence_max_words": self.first_sentence_max_words,
        }


class PersonaRegistry:
    """Loads + holds persona definitions. Picks one as 'active' at any moment."""

    def __init__(self, personas_dir: Path = PERSONAS_DIR):
        self._dir = personas_dir
        self._personas: Dict[str, Persona] = {}
        self._active_id: Optional[str] = None
        self.load()

    def load(self) -> None:
        self._personas.clear()
        if not self._dir.exists():
            logger.warning("🎭⚠️ Personas dir not found at %s", self._dir)
            return
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                persona = Persona(
                    name=data["name"],
                    title=data.get("title", data["name"]),
                    voice_id=data["voice_id"],
                    voice_name=data.get("voice_name", data["name"]),
                    model=data.get("model", "claude-sonnet-4-6"),
                    ring_color=data.get("ring_color", "#14e3ff"),
                    system_prompt=data["system_prompt"],
                    order=data.get("order", 99),
                    default=data.get("default", False),
                    voice_needs_clone=data.get("voice_needs_clone", False),
                    first_sentence_max_words=data.get("first_sentence_max_words"),
                    extra={k: v for k, v in data.items() if k not in {
                        "name", "title", "voice_id", "voice_name", "model",
                        "ring_color", "system_prompt", "order", "default",
                        "voice_needs_clone", "first_sentence_max_words",
                    }},
                )
                self._personas[persona.id] = persona
                logger.info("🎭✅ Loaded persona '%s' (voice=%s, color=%s)",
                            persona.name, persona.voice_name, persona.ring_color)
            except Exception as exc:
                logger.error("🎭💥 Failed to load persona %s: %s", path.name, exc)
        # Determine default active persona
        if not self._active_id:
            default_persona = next(
                (p for p in self._personas.values() if p.default),
                None,
            )
            if not default_persona:
                # Fall back to lowest-order persona
                ordered = sorted(self._personas.values(), key=lambda p: p.order)
                default_persona = ordered[0] if ordered else None
            if default_persona:
                self._active_id = default_persona.id
                logger.info("🎭🌟 Default persona: %s", default_persona.name)

    def all(self) -> List[Persona]:
        return sorted(self._personas.values(), key=lambda p: p.order)

    def get(self, persona_id: Optional[str] = None) -> Optional[Persona]:
        if persona_id is None:
            persona_id = self._active_id
        if not persona_id:
            return None
        return self._personas.get(persona_id.lower())

    def set_active(self, persona_id: str) -> Optional[Persona]:
        persona = self._personas.get(persona_id.lower())
        if persona is None:
            logger.warning("🎭⚠️ Unknown persona '%s' — keeping %s", persona_id, self._active_id)
            return None
        self._active_id = persona.id
        logger.info("🎭🔁 Active persona switched to %s", persona.name)
        return persona

    @property
    def active_id(self) -> Optional[str]:
        return self._active_id


# Process-wide singleton
registry = PersonaRegistry()
