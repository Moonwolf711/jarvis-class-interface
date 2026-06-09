"""Parakeet final-transcription backend for Jarvis.

This is an *optional, additive* speech-to-text backend that re-transcribes a
captured utterance with NVIDIA Parakeet via mudler/parakeet.cpp (vendored at
``third_party/parakeet.cpp``). It does NOT replace RealtimeSTT: RealtimeSTT
still owns mic capture, VAD, realtime partials and turn detection. When enabled
(``STT_FINAL_ENGINE=parakeet``), TranscriptionProcessor routes the finished
utterance's audio here and uses Parakeet's transcript as the *final* result,
falling back to whisper on any failure.

IMPORTANT — read before believing this is "faster":
  * STT is already local in Jarvis (realtimestt / faster-whisper). This backend
    does NOT remove a cloud dependency. It *adds* compute (whisper still runs
    partials + its own final; Parakeet runs an extra final). The point is an
    accuracy A/B: Parakeet's final vs whisper's final.
  * This subprocess path spawns ``parakeet-cli`` per utterance, which RELOADS
    the GGUF model every call (no offline daemon mode). That model-load
    (~0.5-2s+ for a 0.6B model, worse on cold cache / CPU-only) dominates and is
    NOT viable for real-time turn latency. It exists to PROVE transcript quality.
    The latency-viable path is the parakeet.cpp C-API (load model once, keep ctx
    warm) via ctypes against libparakeet — see scripts/build_parakeet.ps1. Treat
    this module as the quality probe, not the production hot path.

Config (env):
  STT_FINAL_ENGINE   "whisper" (default) | "parakeet"   -- read by transcribe.py
  PARAKEET_BIN       path to parakeet-cli[.exe]
                     (default: third_party/parakeet.cpp/build/examples/cli/parakeet-cli[.exe])
  PARAKEET_MODEL     path to a .gguf model (required when engine=parakeet)
  PARAKEET_DECODER   "" (model default) | "tdt" | "ctc"
  PARAKEET_TIMEOUT_S subprocess hard timeout in seconds (default 8.0)

License: parakeet.cpp is MIT. Model weights carry NVIDIA's Parakeet licenses --
check each HF model card before shipping.
"""

import json
import logging
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE: int = 16000
INT16_MAX_ABS_VALUE: float = 32768.0

# Repo-relative default location of the built CLI.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BIN_REL = Path("third_party") / "parakeet.cpp" / "build" / "examples" / "cli"
_BIN_NAME = "parakeet-cli.exe" if os.name == "nt" else "parakeet-cli"


def _default_bin() -> Path:
    return _REPO_ROOT / _DEFAULT_BIN_REL / _BIN_NAME


class ParakeetTranscriber:
    """Subprocess wrapper around ``parakeet-cli transcribe ... --json``.

    Quality probe, not a latency path (see module docstring). Every public call
    is bounded by a hard timeout and returns ``None`` on any failure so the
    caller can fall back to whisper without ever blocking the voice turn.
    """

    def __init__(
        self,
        binary: Optional[str] = None,
        model: Optional[str] = None,
        decoder: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ) -> None:
        self.binary = Path(binary) if binary else _default_bin()
        self.model = Path(model) if model else None
        self.decoder = (decoder or "").strip().lower()
        self.timeout_s = float(timeout_s) if timeout_s is not None else 8.0

    @classmethod
    def from_env(cls) -> "ParakeetTranscriber":
        return cls(
            binary=os.getenv("PARAKEET_BIN"),
            model=os.getenv("PARAKEET_MODEL"),
            decoder=os.getenv("PARAKEET_DECODER"),
            timeout_s=float(os.getenv("PARAKEET_TIMEOUT_S", "8.0")),
        )

    def is_available(self) -> bool:
        """True only if the built binary and a model file both exist on disk."""
        if not self.binary.is_file():
            logger.warning(
                "👂🦜 Parakeet binary not found at %s -- run scripts/build_parakeet.ps1",
                self.binary,
            )
            return False
        if not self.model or not self.model.is_file():
            logger.warning(
                "👂🦜 Parakeet model not found (PARAKEET_MODEL=%s) -- run scripts/fetch_parakeet_model.ps1",
                self.model,
            )
            return False
        return True

    def transcribe(self, audio_f32: Optional[np.ndarray]) -> Optional[str]:
        """Transcribe a mono 16 kHz float32 buffer (range [-1, 1]).

        Returns the transcript string, or ``None`` on empty input, missing
        binary/model, timeout, nonzero exit, or unparseable output. ``None``
        always means "caller should fall back to whisper".
        """
        if audio_f32 is None or len(audio_f32) == 0:
            return None
        if not self.is_available():
            return None

        wav_path: Optional[str] = None
        try:
            wav_path = self._write_wav(audio_f32)
            cmd = [str(self.binary), "transcribe", "--model", str(self.model),
                   "--input", wav_path, "--json"]
            if self.decoder in ("tdt", "ctc"):
                cmd += ["--decoder", self.decoder]

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
            if proc.returncode != 0:
                logger.error(
                    "👂🦜 parakeet-cli exit %s: %s",
                    proc.returncode,
                    (proc.stderr or "").strip()[:300],
                )
                return None

            text = self._parse_text(proc.stdout)
            if text:
                logger.info("👂🦜 Parakeet final: %s", text)
            return text or None
        except subprocess.TimeoutExpired:
            logger.warning(
                "👂🦜 parakeet-cli timed out after %.1fs -- falling back to whisper",
                self.timeout_s,
            )
            return None
        except Exception as exc:  # never let STT crash the voice turn
            logger.error("👂🦜 Parakeet transcribe failed: %s", exc, exc_info=True)
            return None
        finally:
            if wav_path:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

    @staticmethod
    def _parse_text(stdout: str) -> str:
        """Extract ``text`` from --json output; tolerate leading log lines."""
        stdout = (stdout or "").strip()
        if not stdout:
            return ""
        # --json prints a single JSON object; if anything precedes it, grab the
        # last brace-delimited span.
        try:
            return str(json.loads(stdout).get("text", "")).strip()
        except json.JSONDecodeError:
            start, end = stdout.find("{"), stdout.rfind("}")
            if 0 <= start < end:
                try:
                    return str(json.loads(stdout[start:end + 1]).get("text", "")).strip()
                except json.JSONDecodeError:
                    pass
        logger.error("👂🦜 Could not parse parakeet-cli JSON output.")
        return ""

    @staticmethod
    def _write_wav(audio_f32: np.ndarray) -> str:
        """Write a float32 [-1,1] buffer to a temp 16 kHz mono int16 WAV."""
        pcm = np.clip(audio_f32 * INT16_MAX_ABS_VALUE, -INT16_MAX_ABS_VALUE,
                      INT16_MAX_ABS_VALUE - 1).astype(np.int16)
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis_parakeet_")
        os.close(fd)
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
        return path


def build_parakeet_backend(backend: Optional[str] = None):
    """Return an available Parakeet STT backend, or None.

    Both backends duck-type the same interface: ``is_available() -> bool`` and
    ``transcribe(audio_f32) -> Optional[str]``.

    backend (or env PARAKEET_BACKEND):
      "auto" (default) -- prefer the warm C-API binding (load once, fast),
                          fall back to the subprocess CLI (per-call reload).
      "capi"           -- C-API only.
      "cli"            -- subprocess CLI only.
    """
    backend = (backend or os.getenv("PARAKEET_BACKEND", "auto")).strip().lower()
    candidates = []
    if backend in ("auto", "capi"):
        try:
            from parakeet_capi import ParakeetCAPI
            candidates.append(ParakeetCAPI.from_env())
        except Exception as exc:
            logger.error("👂🦜 Could not import C-API backend: %s", exc)
    if backend in ("auto", "cli"):
        candidates.append(ParakeetTranscriber.from_env())
    for candidate in candidates:
        if candidate.is_available():
            return candidate
    return None
