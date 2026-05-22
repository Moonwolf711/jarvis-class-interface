# Goal — Bring TEN + Vision-Agents into Sophia / Jarvis AI

**Elevator pitch:** Sophia keeps her brain (Claude) and her body (the Jarvis UI), but gains a council of personas she can step between, eyes that watch the webcam, and a pluggable processor spine borrowed from Pipecat/Vision-Agents/TEN.

---

## What we're borrowing, from where

| Source | Pattern lifted | Where it lands in Sophia |
|--------|----------------|--------------------------|
| **TEN Framework** | Extension model — capability declared in property files; graph of agents | `personas/*.yaml` registry + `processors/` plugin slots |
| **Vision-Agents** | Real-time webcam frame pipeline + native Claude multimodal | New WS frame stream → image content blocks on Anthropic call |
| **Pipecat** | Frame-based modular pipeline (stages compose) | `ProcessorChain` class wrapping the existing audio→STT→LLM→TTS path |
| **LiveKit Agents** | Multi-agent in a single room, worker model | `CouncilMode` — multiple personas respond in turn, each in own voice |
| **Dograh** | Workflow as data (JSON config of nodes) | Personas + processor wiring loaded from YAML at boot, no code change to swap |

---

## User-visible behavior

1. **Persona picker chip** appears in the top-center HUD next to the title ribbon. Click it → dropdown of personas (Sophia, Leonidas, Apollo, LuSiD, Christina, Emilia). Pick one mid-call, the very next response speaks in that voice with that personality. The orb's accent color shifts to the persona's signature hue (Sophia=cyan, Leonidas=ember, Apollo=gold, etc.).
2. **Vision toggle button** sits next to ENGAGE. Click → tab requests camera permission → a small webcam tile renders inside the holo-frame slot on the left. Frames go up to Claude on each turn. If Tyler is holding a Spartan helmet, Sophia comments on the helmet without him saying it.
3. **Council mode toggle** — a third bottom-bar button. When on, the persona picker becomes a multi-select. Selected personas respond in turn to each user turn. The active speaker's name appears in the BR HUD; the orb ring color cycles to whoever's voice is playing.
4. **No regressions** — solo Sophia mode keeps working exactly as it does now. New modes are opt-in.

---

## Files to touch (verified to exist or new)

### Backend (Python)
- **NEW** `code/personas/sophia.yaml`, `leonidas.yaml`, `apollo.yaml`, `lusid.yaml`, `christina.yaml`, `emilia.yaml` — each has `voice_id`, `model`, `system_prompt`, `ring_color`, `processors[]`
- **NEW** `code/persona_registry.py` — loads YAMLs, exposes `get(name) -> Persona`, hot-reload on file change
- **NEW** `code/processors/__init__.py` + at least one example processor (`vision_attach.py`)
- **NEW** `code/council.py` — multi-agent turn-taking coordinator
- **EDIT** `code/server.py` — new WS message types: `set_persona`, `set_mode`, `vision_frame`, `toggle_vision`. Plumb into SpeechPipelineManager.
- **EDIT** `code/speech_pipeline_manager.py` — accept a Persona object, swap voice + system_prompt at the boundary of each generation. Apply processor chain before Anthropic call.
- **EDIT** `code/audio_module.py` — voice swap via ElevenLabsEngine.id at runtime (not just at engine construction)
- **EDIT** `code/llm_module.py` — accept per-generation system_prompt override + extra image content blocks

### Frontend (HTML/JS in `code/static/`)
- **EDIT** `code/static/index.html`:
  - Add persona picker chip near title ribbon
  - Add vision toggle button + `<video>` tile + `<canvas>` frame extractor
  - Add council mode toggle in bottom bar
  - JS to capture webcam frames every ~2s, downscale, send via WS
  - JS to receive `active_persona` updates → re-color the orb rings

### Config / infra
- **EDIT** `docker-compose.yml` — mount `personas/` directory into container alongside `code/`
- **NEW** `tasks/goal-sophia-multimodal.md` (this file)
- **NEW** `tasks/lessons.md` — captured failure modes as we hit them

### NOT touching
- Nothing in `~/voice-frameworks/*` — we're borrowing concepts, not code
- Nothing in `~/wavdrop` — different project entirely
- Nothing about Codemagic / TestFlight / iOS — Sophia is a docker container, not an app

---

## State model

- **Persona** (Pydantic) — `{name, voice_id, model, system_prompt, ring_color, processors: list[str]}`
- **Session state** (per WS connection) — `{active_persona: str, council_members: list[str], council_mode: bool, vision_active: bool, last_frame_b64: str | None}`
- **Vision frame buffer** — server holds the latest webcam frame per session. Next Anthropic call attaches it as an image block if `vision_active`. Frame replaced on each new push.

Mutations:
- `set_persona(name)` → `active_persona = name`. Takes effect on next generation.
- `set_mode(mode)` → `council_mode = (mode == "council")`. Council uses `council_members` list.
- `toggle_vision(on)` → `vision_active = on`. Toggles whether last_frame attaches to next call.
- `vision_frame(b64)` → updates `last_frame_b64`.

---

## Edge cases

1. **Mid-turn persona switch** — generation is already running. We let the current generation finish in the OLD voice, then the next one uses the new voice. No mid-utterance voice-swap (causes audio glitches).
2. **Council mode with 1 selected member** — degrades to solo mode for that turn. No "director" overhead.
3. **Council mode with 0 members** — falls back to Sophia (default).
4. **Vision active but webcam unavailable** — fail soft: skip frame attachment, no crash, log warning.
5. **Frame too large** — server-side cap at 512x512 JPEG, ~100KB. Reject > 500KB silently.
6. **Vision + LuSiD voice** — vision content block goes to Claude regardless of voice. LuSiD just narrates what Claude sees.
7. **Lost WS during council reply chain** — abort all pending speakers, clean state.
8. **Persona YAML malformed** — log error at boot, fall back to Sophia, expose error in `/healthz`.

---

## Acceptance criteria

- [ ] **A1.** GET `http://localhost:8181/` shows the persona picker chip in the top HUD. Dropdown lists ≥4 personas.
- [ ] **A2.** Clicking a non-default persona → next reply audibly comes through Komplete in that voice.
- [ ] **A3.** Orb ring color matches the active persona's `ring_color` within 200ms of selection.
- [ ] **A4.** Vision toggle requests camera. On allow, a webcam tile renders inside the left holo-frame. Frames push at ~0.5 Hz.
- [ ] **A5.** Holding a recognizable object in front of webcam → Sophia mentions it without prompt (proving frames reach Claude).
- [ ] **A6.** Council toggle on, two personas selected → after a single user turn, both personas respond in their own voices, in selection order, no overlap.
- [ ] **A7.** With everything off (vision off, council off, default persona), Sophia behaves identically to current `master` of this repo.
- [ ] **A8.** Docker compose restart succeeds. Container healthy. No new dependencies that aren't in `requirements.txt`.
- [ ] **A9.** Tasks 2–5 each get their own commit with a single-concern message. Push to `main`.

---

## Out of scope (will NOT do in this pipeline)

- TEN Framework runtime install — we lift its pattern only, not its 30k files.
- Vision-Agents library install — we copy its concept, not its `uv add vision-agents` dependency.
- Dograh's React drag-drop UI — JSON config is enough for v1.
- YOLO/Roboflow vision pre-processors — slot reserved but no model wired in v1. Claude vision alone is plenty for the demo.
- LiveKit RTC transport — Sophia stays on the existing browser WS. We borrow the multi-agent room *concept*, not the WebRTC stack.
- MCP tool calling for personas — slot reserved in processor chain, no tools wired in v1.
- TestFlight / mobile build — Sophia is browser-only.
- Persistence of conversation across restarts — out of scope.

---

## Load-bearing assumptions

- **Claude Sonnet 4.6 sees images natively.** Verified — `claude-sonnet-4-6` accepts `image` content blocks. If wrong, vision intake gracefully no-ops.
- **ElevenLabsEngine accepts mid-stream voice changes.** Verified-by-restart only — easiest path is to recreate the engine when persona changes (small latency hit, acceptable).
- **Browser webcam capture works in Brave at `localhost:8181`.** Brave allows mic+camera on localhost; same MediaDevices API as Chrome.
- **Docker volume mount of `personas/` survives compose restart.** Standard.
- **No regression risk to current Sophia-only flow.** All new behavior gated behind toggles; default == current behavior.

If any of these breaks, stop and flag — don't paper over it.

---

## Build order (sequential — each step shippable)

1. **Persona registry** (Task #2) — pure backend + tiny UI chip. No new infra. ~45 min.
2. **Vision intake** (Task #3) — webcam → WS → Claude. Larger but isolated. ~60 min.
3. **Pluggable processors** (Task #4) — refactor the persona+vision wiring into a clean processor chain. ~45 min.
4. **Council mode** (Task #5) — most complex, depends on #1 working. ~75 min.
5. **Verify + approval** (Task #6) — screenshots at the 393×852-equivalent browser, send via `SendUserFile`. Wait for Tyler's "go."
6. **Commit + push** (Task #7) — five commits, batched by concern. No TestFlight (not iOS).

Total estimated dev time: ~3.75 hours focused.

---

**Awaiting Tyler's approval before any code touches `code/`.**
