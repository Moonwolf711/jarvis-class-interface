"""Council Mode — multiple personas respond to a single user turn in sequence.

Borrows the LiveKit "agents in a room" pattern, simplified for the existing
SpeechPipelineManager. The coordinator drives one persona at a time, waits
for that turn's TTS to clear, swaps to the next persona, and re-issues the
same user text. The final persona restores the originally active persona.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class CouncilCoordinator:
    """Sequential multi-persona reply orchestrator."""

    def __init__(self, pipeline_manager):
        self.pm = pipeline_manager
        self.enabled: bool = False
        self.members: List[str] = []
        self._lock = asyncio.Lock()

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "members": list(self.members)}

    def set_enabled(self, on: bool) -> dict:
        self.enabled = bool(on)
        logger.info(f"🎭🏛️ Council mode: {'ON' if self.enabled else 'OFF'} (members={self.members})")
        return self.to_dict()

    def set_members(self, members: List[str]) -> dict:
        clean = []
        registry = getattr(self.pm, "_persona_registry", None)
        for m in members or []:
            if registry and registry.get(m):
                clean.append(m.lower())
        # dedupe preserving order
        seen, out = set(), []
        for m in clean:
            if m not in seen:
                seen.add(m); out.append(m)
        self.members = out
        logger.info(f"🎭🏛️ Council members set: {self.members}")
        return self.to_dict()

    async def run_turn(self, text: str, on_speaker_change=None) -> None:
        """Drive each persona's reply in sequence to the same user text.

        Each persona sees the SAME history (the snapshot taken when the council
        turn began) — previous council members' replies are not visible to the
        next speaker. Prevents "history bleed" where Christina reacts to
        Leonidas's reply instead of the original prompt.

        on_speaker_change(persona_dict) — may be sync OR async — is awaited if a coroutine.
        """
        async with self._lock:
            if not self.enabled or not self.members:
                return
            registry = getattr(self.pm, "_persona_registry", None)
            if registry is None:
                logger.warning("🎭🏛️ Council asked to run but persona registry missing")
                return
            saved_id = registry.active_id
            # Snapshot history BEFORE any council member runs. Each member resets
            # the live history to this snapshot before generating, so none of them
            # sees the previous council member's reply.
            saved_history = [dict(m) for m in list(self.pm.history)]
            logger.info(f"🎭🏛️ Council start — {len(self.members)} members, history snapshot len={len(saved_history)}")
            try:
                for idx, member_id in enumerate(self.members):
                    persona = registry.get(member_id)
                    if persona is None:
                        continue
                    # Reset history for THIS council member to the pre-council state
                    self.pm.history[:] = [dict(m) for m in saved_history]
                    # Swap persona
                    res = self.pm.apply_persona(member_id)
                    if on_speaker_change:
                        try:
                            ret = on_speaker_change(res or persona.to_dict())
                            if asyncio.iscoroutine(ret):
                                await ret
                        except Exception as cb_e:
                            logger.warning(f"🎭🏛️ on_speaker_change error: {cb_e}")
                    logger.info(f"🎭🏛️ Council [{idx+1}/{len(self.members)}] → {persona.name} replying")
                    self.pm.prepare_generation(text)
                    await self._await_completion(timeout=30)
            finally:
                # Restore the originally active persona AND collapse history back to
                # the pre-council snapshot (the council exchange itself doesn't
                # persist; the next solo turn starts clean).
                if saved_id:
                    self.pm.apply_persona(saved_id)
                self.pm.history[:] = [dict(m) for m in saved_history]
                logger.info(f"🎭🏛️ Council end — restored persona={saved_id}, history len={len(self.pm.history)}")

    async def _await_completion(self, timeout: float = 30.0) -> None:
        """Poll until the current generation finishes (or timeout)."""
        start = asyncio.get_event_loop().time()
        # First wait for a generation to actually start
        for _ in range(40):
            if self.pm.running_generation is not None:
                break
            await asyncio.sleep(0.05)
        # Then wait for it to clear
        while self.pm.running_generation is not None:
            if asyncio.get_event_loop().time() - start > timeout:
                logger.warning("🎭🏛️ Council member exceeded timeout, moving on")
                return
            await asyncio.sleep(0.25)
        # Pad a small gap so audio doesn't clip into next speaker
        await asyncio.sleep(0.6)
