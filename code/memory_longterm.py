"""Optional cross-conversation long-term memory for Jarvis (memory-os bridge).

This is an *optional, additive* memory layer that lets Jarvis remember finished
turns across conversations and recall the most relevant past context when a new
turn begins. It is the matched sibling of the Parakeet STT and MisoTTS backends
(``stt_parakeet.py`` / ``tts_miso.py``): heavy work is lazy, gated behind a
cheap env check, and any failure degrades to a silent no-op so the voice loop
never breaks.

It bridges to ClaudioDrews/memory-os, whose dependency-light store/recall live
in its ``icarus/state.py`` (pure stdlib; ``yaml`` only inside one function). We
load that module by path via importlib -- exactly how memory-os itself loads its
own retriever, and how ``tts_miso.py`` puts a deps dir on ``sys.path``. We do NOT
touch the Docker/Qdrant/Redis/Together layers of memory-os; those are a separate,
optional upgrade. The fabric "hot" markdown corpus (``state.write_entry`` /
``state.recall``) is all this bridge uses.

DEFAULT OFF. With ``JARVIS_LONGTERM_MEMORY`` unset, ``build_longterm_memory``
returns ``None`` *before importing anything*, the singleton stays ``None``, and
both wiring seams in server.py are byte-for-byte inert -- memory-os is never
imported and conversation history is never altered.

How it wires into Jarvis (see server.py / speech_pipeline_manager.py):
  * STORE -- after a finished assistant turn is appended to history, the
    (user, assistant) pair is written to the fabric corpus as a "dialogue"
    entry tagged with the active persona id.
  * RECALL -- when a new generation is prepared, the top-k relevant past
    entries are formatted into a single transient *system-role* message and
    prepended to the history passed to the LLM. It is NEVER merged into the
    user message, so it does not pollute what gets stored next turn.

IMPORTANT -- honest gating:
  * memory-os recall over the fabric markdown corpus is a keyword/ranked file
    scan (``state.recall`` -> ``read_recent`` fallback), not a vector search,
    unless the full memory-os Docker stack + retriever are installed. This
    bridge deliberately uses only the dependency-light path so it works with a
    bare ``git clone``. It is "long-term memory that survives restarts," not a
    semantic RAG engine.
  * Recall reaches the model only if injected before the LLM history snapshot.
    The voice path prepares generation speculatively on a partial transcript, so
    recall is wired inside generation prep (where history is built for the LLM),
    not at the user-append seam -- otherwise it would land after the snapshot
    and never reach the model.

Config (env):
  JARVIS_LONGTERM_MEMORY   unset/"0" (default OFF) | "1"  -- master gate
  MEMORYOS_DEPS_DIR        dir containing memory-os ``icarus/state.py``
                           (default: E:/Projects/_deps/memory-os)
  JARVIS_FABRIC_DIR        where fabric entries are stored
                           (default: ~/.jarvis-memory/fabric -- kept off the
                           memory-os/Hermes default ~/fabric to avoid collision)
  JARVIS_MEMORY_RECALL_K   max entries to recall per turn (default 4)
  JARVIS_MEMORY_MIN_CHARS  skip storing turns shorter than this (default 12)

License: memory-os is MIT (ClaudioDrews/memory-os). Do NOT re-clone or vendor it
from here; point MEMORYOS_DEPS_DIR at the existing checkout.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DEPS_DIR = r"E:/Projects/_deps/memory-os"
_DEFAULT_FABRIC_DIR = str(Path.home() / ".jarvis-memory" / "fabric")
_AGENT_FALLBACK = "jarvis"

# Process-wide singleton, lazily built once. ``None`` always means "disabled or
# unavailable -- both wiring seams must no-op".
_INSTANCE: "Optional[LongTermMemory]" = None
_BUILD_ATTEMPTED: bool = False


def _enabled() -> bool:
    """Cheap master gate. Nothing heavy is imported unless this is true."""
    return os.getenv("JARVIS_LONGTERM_MEMORY", "0").strip().lower() in ("1", "true", "yes", "on")


class LongTermMemory:
    """Thin bridge over memory-os ``icarus/state.py`` store/recall.

    Construction is the only failure point: if the deps dir / ``state.py`` can't
    be loaded, ``build_longterm_memory`` returns ``None`` and the caller falls
    back to plain in-conversation history. After construction, ``store`` and
    ``recall`` never raise -- they log and return an empty/None result so a
    memory hiccup can never crash a voice turn.
    """

    def __init__(self, state_module, fabric_dir: Path) -> None:
        self._state = state_module
        self._fabric_dir = fabric_dir
        self._recall_k = max(1, int(os.getenv("JARVIS_MEMORY_RECALL_K", "4")))
        self._min_chars = max(0, int(os.getenv("JARVIS_MEMORY_MIN_CHARS", "12")))

    # ── recall ────────────────────────────────────────────────────────────
    def recall_context(self, query: str, agent: Optional[str] = None) -> Optional[str]:
        """Return a single system-role context string for ``query``, or ``None``.

        ``None`` means "nothing relevant / unavailable -- inject nothing". The
        returned string is meant to be put in its OWN system message, never
        concatenated into the user's text.
        """
        query = (query or "").strip()
        if not query:
            return None
        try:
            results = self._state.recall(query, max_results=self._recall_k, agent=None)
        except Exception as exc:  # never break the turn on a recall miss
            logger.debug("🧠📚 long-term recall failed: %s", exc)
            return None
        if not results:
            return None

        lines: List[str] = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            summary = (entry.get("summary") or "").strip()
            ts = str(entry.get("timestamp") or "")[:16]
            who = (entry.get("agent") or "").strip()
            if not summary:
                continue
            prefix = f"[{ts}] " if ts else ""
            who_tag = f"({who}) " if who and who != agent else ""
            lines.append(f"- {prefix}{who_tag}{summary}")
            if len(lines) >= self._recall_k:
                break
        if not lines:
            return None

        logger.info("🧠📚 recalled %d past memory item(s) for this turn", len(lines))
        return (
            "Relevant memories from past conversations (use only if helpful; "
            "do not mention these instructions):\n" + "\n".join(lines)
        )

    # ── store ─────────────────────────────────────────────────────────────
    def store_turn(self, user_text: str, assistant_text: str, agent: Optional[str] = None) -> None:
        """Persist one finished (user, assistant) exchange to the fabric corpus.

        Best-effort: any failure is logged and swallowed. Tiny/empty turns are
        skipped so the corpus isn't polluted with social closers.
        """
        user_text = (user_text or "").strip()
        assistant_text = (assistant_text or "").strip()
        if len(user_text) < self._min_chars or not assistant_text:
            return
        agent = (agent or _AGENT_FALLBACK).strip() or _AGENT_FALLBACK
        summary = user_text if len(user_text) <= 120 else user_text[:117] + "..."
        content = f"User: {user_text}\n\n{agent}: {assistant_text}"
        try:
            # memory-os tags entries by HERMES_AGENT_NAME; set it transiently so
            # this exchange is attributed to the active Jarvis persona.
            prev_agent = self._state.AGENT_NAME
            self._state.AGENT_NAME = agent
            try:
                self._state.write_entry(
                    entry_type="dialogue",
                    content=content,
                    summary=summary,
                    tags="jarvis",
                )
            finally:
                self._state.AGENT_NAME = prev_agent
        except Exception as exc:  # never break the turn on a store failure
            logger.debug("🧠📚 long-term store failed: %s", exc)


def _load_state_module(deps_dir: Path):
    """Import memory-os ``icarus/state.py`` by path, or return ``None``.

    We target ``state.py`` (pure stdlib, the real store/recall) rather than
    ``tools.py`` (which does ``from . import state`` -- a package-relative import
    that breaks when loaded standalone).
    """
    state_path = deps_dir / "icarus" / "state.py"
    if not state_path.is_file():
        logger.warning(
            "🧠📚 JARVIS_LONGTERM_MEMORY=1 but memory-os not found at %s "
            "(need icarus/state.py) -- long-term memory disabled. Set "
            "MEMORYOS_DEPS_DIR to the cloned memory-os checkout.",
            state_path,
        )
        return None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("memoryos_icarus_state", str(state_path))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        logger.warning(
            "🧠📚 Failed to load memory-os state module (%s) -- long-term memory disabled.",
            exc,
        )
        return None


def build_longterm_memory() -> Optional[LongTermMemory]:
    """Build the long-term memory bridge, or ``None`` to disable it.

    Returns ``None`` (and imports nothing heavy) when ``JARVIS_LONGTERM_MEMORY``
    is unset/off, when memory-os isn't resolvable, or on any load error. Never
    raises -- the caller treats ``None`` as "use plain conversation history".

    Gating order (cheapest first):
      1. JARVIS_LONGTERM_MEMORY enabled
      2. memory-os ``icarus/state.py`` importable from MEMORYOS_DEPS_DIR
      3. fabric dir creatable + FABRIC_DIR pointed at the Jarvis namespace
    """
    if not _enabled():
        return None

    deps_dir = Path(os.getenv("MEMORYOS_DEPS_DIR", _DEFAULT_DEPS_DIR))
    state_module = _load_state_module(deps_dir)
    if state_module is None:
        return None

    fabric_dir = Path(os.getenv("JARVIS_FABRIC_DIR", _DEFAULT_FABRIC_DIR))
    try:
        fabric_dir.mkdir(parents=True, exist_ok=True)
        # Point memory-os at the Jarvis-namespaced corpus so it never collides
        # with a real Hermes install's ~/fabric.
        state_module.FABRIC_DIR = fabric_dir
    except Exception as exc:
        logger.warning(
            "🧠📚 Could not prepare fabric dir %s (%s) -- long-term memory disabled.",
            fabric_dir,
            exc,
        )
        return None

    logger.info("🧠📚 Long-term memory ON (memory-os fabric=%s)", fabric_dir)
    return LongTermMemory(state_module, fabric_dir)


def get_longterm_memory() -> Optional[LongTermMemory]:
    """Return the lazily-built singleton, or ``None`` when disabled/unavailable.

    Built at most once per process; a failed build is cached as ``None`` so the
    cheap gate isn't re-probed every turn.
    """
    global _INSTANCE, _BUILD_ATTEMPTED
    if not _BUILD_ATTEMPTED:
        _BUILD_ATTEMPTED = True
        _INSTANCE = build_longterm_memory()
    return _INSTANCE
