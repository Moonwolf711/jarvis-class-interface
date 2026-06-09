"""Local MisoTTS output backend for Jarvis.

This is an *optional, additive* text-to-speech backend that synthesizes Jarvis's
spoken replies with MisoLabs/MisoTTS-8B entirely on-device. It is the matched
partner to the Parakeet STT backend (``stt_parakeet.py``): together they give a
fully-local voice loop with no cloud TTS/STT round-trip.

It plugs into the existing RealtimeTTS pipeline as a ``BaseEngine`` subclass --
``AudioProcessor`` wraps it in the same ``TextToAudioStream`` as kokoro/coqui/
orpheus/elevenlabs, so ``synthesize`` / ``synthesize_generator`` in
``audio_module.py`` are untouched. MisoTTS emits 24 kHz float32 audio, which is
exactly the rate Jarvis's stream already runs at (16-bit, 24000 Hz, mono), so no
resampling of the output is required.

How it differs from the Parakeet mirror:
  * Parakeet's fallback is *per-utterance* (transcribe -> None -> use whisper).
  * Miso loads the model ONCE and stays warm, so its fallback is *at
    construction time*: if the GPU/weights/sample gate fails, ``build_miso_engine``
    returns ``None`` and ``AudioProcessor`` rebuilds the previously-configured
    default engine instead. ``__init__`` must never raise on account of Miso.

IMPORTANT -- this is GPU-gated and heavy:
  * MisoTTS-8B weights are ~30-40 GB (HF ``MisoLabs/MisoTTS``) and need >=24 GB
    VRAM. The first run blocking-downloads them. We gate the heavy load behind a
    cheap CUDA check so an opt-in without a capable GPU degrades gracefully back
    to the existing engine rather than OOM-ing or hanging on a giant download.
  * Whole-utterance synthesis is NOT a low-latency streaming path (Miso decodes
    the full clip, then it's queued). It exists to PROVE a fully-local voice loop,
    not to beat kokoro's TTFA.

Config (env):
  TTS_ENGINE              "elevenlabs" (Jarvis default) | "miso"  -- read by server.py
  MISO_MODEL              HF repo id or local path/dir of the checkpoint
                          (default: MisoLabs/MisoTTS via the generator's own default)
  MISO_NIGEL_SAMPLE       path to a reference WAV for voice cloning (optional)
  MISO_NIGEL_SAMPLE_TEXT  transcript of MISO_NIGEL_SAMPLE (required for a good clone)
  MISO_DEPS_DIR           dir containing MisoTTS generator.py/models.py
                          (default: E:/Projects/_deps/MisoTTS)
  MISO_DEVICE             "cuda" (default) | "cpu"  -- cpu is impractical but allowed
  MISO_SPEAKER            speaker id int for generate() (default: 0)
  MISO_TEMPERATURE        sampling temperature (default 0.9)
  MISO_TOPK               top-k (default 50)
  MISO_MAX_AUDIO_MS       per-utterance cap in ms (default 30000)

License: MisoTTS carries MisoLabs' model license + an imperceptible watermark on
all generated audio (applied inside the generator). Check the HF model card
before shipping. Do NOT re-clone the repo or re-download weights from here.
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# MisoTTS native sample rate; matches Jarvis's 24 kHz int16 stream exactly.
MISO_SAMPLE_RATE: int = 24000
_DEFAULT_DEPS_DIR = r"E:/Projects/_deps/MisoTTS"
_INT16_MAX = 32767.0


def _cuda_available() -> bool:
    """Cheap CUDA probe that never raises and avoids importing torch if absent."""
    try:
        import torch  # heavy, but only when we're actually considering Miso
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def build_miso_engine():
    """Build a warm MisoTTS RealtimeTTS engine, or return ``None`` to fall back.

    Returns a ``BaseEngine`` subclass instance ready to drop into
    ``TextToAudioStream``, or ``None`` on any gate failure (no CUDA, deps/weights
    not resolvable, model load error). Never raises -- the caller treats ``None``
    as "use the previously-configured default engine instead".

    Gating order (cheapest first):
      1. CUDA present (unless MISO_DEVICE=cpu is explicitly forced)
      2. MisoTTS deps dir importable (generator.py on sys.path)
      3. Model loads (this is the ~30-40 GB step; may block on first download)
    """
    device = os.getenv("MISO_DEVICE", "cuda").strip().lower()

    if device == "cuda" and not _cuda_available():
        logger.warning(
            "👄🌸 TTS_ENGINE=miso but no CUDA GPU available -- falling back to the "
            "configured default engine. Set MISO_DEVICE=cpu to force (impractical)."
        )
        return None

    # Put the MisoTTS deps dir on sys.path so `from generator import ...` resolves.
    deps_dir = Path(os.getenv("MISO_DEPS_DIR", _DEFAULT_DEPS_DIR))
    if not deps_dir.is_dir() or not (deps_dir / "generator.py").is_file():
        logger.warning(
            "👄🌸 MisoTTS deps not found at %s (need generator.py) -- falling back. "
            "Set MISO_DEPS_DIR to the cloned MisoTTS checkout.",
            deps_dir,
        )
        return None

    try:
        import sys
        if str(deps_dir) not in sys.path:
            sys.path.insert(0, str(deps_dir))

        # Lazy heavy imports -- kept inside the builder so this module
        # py_compiles cleanly without torch / RealtimeTTS / MisoTTS installed.
        import numpy as np
        import torch
        import torchaudio
        import pyaudio
        from RealtimeTTS.engines import BaseEngine
        from generator import Segment, load_miso_8b  # MisoTTS public API

        model_src = os.getenv("MISO_MODEL") or None  # None -> generator's default repo

        logger.info(
            "👄🌸 Loading MisoTTS-8B (device=%s, model=%s) -- this is heavy; first "
            "run downloads ~30-40 GB.",
            device,
            model_src or "MisoLabs/MisoTTS (default)",
        )
        generator = load_miso_8b(device=device, model_path_or_repo_id=model_src)

        # Optional voice-cloning context from a reference sample.
        clone_context = _build_clone_context(generator, torch, torchaudio)

        class MisoEngine(BaseEngine):
            """RealtimeTTS engine that synthesizes whole utterances with MisoTTS.

            Mirrors ``ElevenLabsPCMEngine`` in audio_module.py: ``get_stream_info``
            advertises 24 kHz / 16-bit / mono, and ``synthesize`` puts raw PCM
            bytes onto ``self.queue`` (no resample -- Miso is already 24 kHz).
            """

            def __init__(self) -> None:
                super().__init__()
                self.engine_name = "miso"
                self._generator = generator
                self._context = clone_context
                self._speaker = int(os.getenv("MISO_SPEAKER", "0"))
                self._temperature = float(os.getenv("MISO_TEMPERATURE", "0.9"))
                self._topk = int(os.getenv("MISO_TOPK", "50"))
                self._max_audio_ms = float(os.getenv("MISO_MAX_AUDIO_MS", "30000"))

            def get_stream_info(self):
                # (pyaudio format, channels, sample_rate) -- matches Jarvis 24 kHz.
                return pyaudio.paInt16, 1, MISO_SAMPLE_RATE

            def synthesize(self, text: str, sentence_count: int = 0) -> bool:
                """Synthesize ``text`` and push 24 kHz int16 PCM to ``self.queue``.

                Returns True on success. On any failure returns False (so the
                stream finishes cleanly) rather than raising into the pipeline.
                """
                self.stop_synthesis_event.clear()
                text = (text or "").strip()
                if not text:
                    return False
                try:
                    audio = self._generator.generate(
                        text=text,
                        speaker=self._speaker,
                        context=self._context,
                        max_audio_length_ms=self._max_audio_ms,
                        temperature=self._temperature,
                        topk=self._topk,
                    )
                    pcm = (
                        audio.detach().to("cpu").float().clamp_(-1.0, 1.0).numpy()
                        * _INT16_MAX
                    ).astype(np.int16)
                    self.queue.put(pcm.tobytes())
                    return True
                except Exception as exc:  # never crash the TTS worker
                    logger.error("👄🌸 MisoTTS synthesize failed: %s", exc, exc_info=True)
                    return False

            def get_voices(self):
                return []

            def set_voice(self, voice) -> None:  # voice cloning is via env sample
                pass

            def set_voice_parameters(self, **voice_parameters) -> None:
                pass

        engine = MisoEngine()
        logger.info(
            "👄🌸 MisoTTS engine ready (24 kHz, cloned=%s).",
            bool(clone_context),
        )
        return engine
    except Exception as exc:
        logger.error(
            "👄🌸 Failed to build MisoTTS engine (%s) -- falling back to the "
            "configured default engine.",
            exc,
            exc_info=True,
        )
        return None


def _build_clone_context(generator, torch, torchaudio):
    """Build a one-shot voice-clone context from MISO_NIGEL_SAMPLE, or ``[]``.

    A missing/unreadable/untranscribed sample yields an empty context: Miso still
    synthesizes, just with its own default voice instead of a clone.
    """
    sample_path = os.getenv("MISO_NIGEL_SAMPLE", "").strip()
    sample_text = os.getenv("MISO_NIGEL_SAMPLE_TEXT", "").strip()
    if not sample_path:
        return []
    if not Path(sample_path).is_file():
        logger.warning("👄🌸 MISO_NIGEL_SAMPLE not found at %s -- no voice clone.", sample_path)
        return []
    if not sample_text:
        logger.warning(
            "👄🌸 MISO_NIGEL_SAMPLE_TEXT empty -- a clone without its transcript is "
            "poor; using default voice instead."
        )
        return []
    try:
        from generator import Segment
        wav, sr = torchaudio.load(sample_path)
        # Mono: average channels -> (num_samples,)
        if wav.dim() == 2:
            wav = wav.mean(dim=0)
        wav = wav.squeeze()
        if sr != MISO_SAMPLE_RATE:
            wav = torchaudio.functional.resample(wav, orig_freq=sr, new_freq=MISO_SAMPLE_RATE)
        wav = wav.to(generator.device)
        logger.info("👄🌸 Voice clone sample loaded from %s (%d samples).", sample_path, wav.numel())
        return [Segment(speaker=0, text=sample_text, audio=wav)]
    except Exception as exc:
        logger.error("👄🌸 Failed to load voice clone sample: %s -- using default voice.", exc)
        return []
