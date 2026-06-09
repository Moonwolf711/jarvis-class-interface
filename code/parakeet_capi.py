"""Phase-2 ctypes binding for parakeet.cpp's C-API — the latency-viable path.

Unlike stt_parakeet.ParakeetTranscriber (which spawns parakeet-cli and reloads
the GGUF every call), this loads the model ONCE into a warm `parakeet_ctx` and
transcribes in-memory float PCM per utterance via parakeet_capi_transcribe_pcm.
No subprocess, no per-call model load, no temp WAV. This is the path you want
for real-time turns.

Duck-types ParakeetTranscriber: exposes is_available() -> bool and
transcribe(audio_f32) -> Optional[str], so TranscriptionProcessor can use either
backend interchangeably (see stt_parakeet.build_parakeet_backend).

Requires the SHARED library, built with:
    pwsh scripts/build_parakeet.ps1 -Shared      # -> build-shared/libparakeet.{dll,so}

Config (env):
  PARAKEET_LIB       full path to libparakeet.{dll,so,dylib} (else auto-searched
                     under third_party/parakeet.cpp/build-shared)
  PARAKEET_MODEL     path to a .gguf model (required)
  PARAKEET_DECODER   "" (default) | "ctc" | "tdt"/"rnnt"

FFI safety notes (why the binding is written the way it is):
  * transcribe_pcm's restype is c_void_p, NOT c_char_p. c_char_p would make
    ctypes copy to bytes and discard the original pointer, leaking the malloc'd
    string. We keep the pointer, read it with string_at, then free_string it.
  * On Windows libparakeet.dll depends on ggml*.dll; those dirs are added via
    os.add_dll_directory before load, or the CDLL load fails with a cryptic
    "DLL load failed".
  * The C-API never lets a C++ exception cross the boundary; failures come back
    as NULL + a last_error string. Any failure here returns None so the caller
    falls back to whisper without ever blocking the voice turn.

License: parakeet.cpp MIT; model weights carry NVIDIA's Parakeet licenses.
"""

import ctypes
import logging
import os
import sys
import threading
from ctypes import POINTER, c_char_p, c_float, c_int, c_void_p
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE: int = 16000
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PK_ROOT = _REPO_ROOT / "third_party" / "parakeet.cpp"

_DECODER_MAP = {"": 0, "ctc": 1, "tdt": 2, "rnnt": 2}


def _lib_filenames() -> list[str]:
    if sys.platform == "win32":
        return ["parakeet.dll", "libparakeet.dll"]
    if sys.platform == "darwin":
        return ["libparakeet.dylib", "libparakeet.so"]
    return ["libparakeet.so"]


def _find_library() -> Optional[Path]:
    """Resolve the shared lib: PARAKEET_LIB env, then common build-shared dirs."""
    env = os.getenv("PARAKEET_LIB")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    search_dirs = [
        _PK_ROOT / "build-shared",
        _PK_ROOT / "build-shared" / "Release",
        _PK_ROOT / "build-shared" / "bin",
        _PK_ROOT / "build-shared" / "bin" / "Release",
        _PK_ROOT / "build" / "src",
    ]
    for d in search_dirs:
        for name in _lib_filenames():
            cand = d / name
            if cand.is_file():
                return cand
    return None


class ParakeetCAPI:
    """Warm-context ctypes binding. Lazy: the lib + model load on first use."""

    def __init__(
        self,
        lib_path: Optional[str] = None,
        model: Optional[str] = None,
        decoder: Optional[str] = None,
    ) -> None:
        self.lib_path = Path(lib_path) if lib_path else _find_library()
        self.model = Path(model) if model else None
        self.decoder_code = _DECODER_MAP.get((decoder or "").strip().lower(), 0)

        self._lib: Optional[ctypes.CDLL] = None
        self._ctx: Optional[int] = None
        self._load_failed = False
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "ParakeetCAPI":
        return cls(
            lib_path=os.getenv("PARAKEET_LIB"),
            model=os.getenv("PARAKEET_MODEL"),
            decoder=os.getenv("PARAKEET_DECODER"),
        )

    def is_available(self) -> bool:
        """Cheap check: shared lib + model file both exist (no dlopen here)."""
        if not self.lib_path or not self.lib_path.is_file():
            logger.warning(
                "👂🦜 libparakeet not found -- build it: scripts/build_parakeet.ps1 -Shared"
            )
            return False
        if not self.model or not self.model.is_file():
            logger.warning(
                "👂🦜 Parakeet model not found (PARAKEET_MODEL=%s)", self.model
            )
            return False
        return True

    def transcribe(self, audio_f32: Optional[np.ndarray]) -> Optional[str]:
        """Transcribe a mono 16 kHz float32 buffer via the warm context.

        Returns the transcript, or None on empty input / load failure / C-API
        error. None always means "fall back to whisper".
        """
        if audio_f32 is None or len(audio_f32) == 0:
            return None
        with self._lock:
            if not self._ensure_loaded():
                return None
            assert self._lib is not None and self._ctx is not None

            arr = np.ascontiguousarray(audio_f32, dtype=np.float32)
            n = int(arr.shape[0])
            ptr = arr.ctypes.data_as(POINTER(c_float))
            try:
                res = self._lib.parakeet_capi_transcribe_pcm(
                    self._ctx, ptr, n, SAMPLE_RATE, self.decoder_code
                )
            except Exception as exc:  # never crash the voice turn
                logger.error("👂🦜 C-API transcribe raised: %s", exc, exc_info=True)
                return None
            # `arr` must outlive the call above; it does (still referenced here).
            if not res:
                logger.error("👂🦜 C-API transcribe failed: %s", self._last_error())
                return None
            try:
                text = ctypes.string_at(res).decode("utf-8", "replace").strip()
            finally:
                self._lib.parakeet_capi_free_string(c_void_p(res))
            if text:
                logger.info("👂🦜 Parakeet(C-API) final: %s", text)
            return text or None

    def close(self) -> None:
        with self._lock:
            if self._lib is not None and self._ctx is not None:
                try:
                    self._lib.parakeet_capi_free(self._ctx)
                except Exception:
                    pass
            self._ctx = None

    def __del__(self) -> None:  # best-effort cleanup
        try:
            self.close()
        except Exception:
            pass

    # --- internals ---

    def _ensure_loaded(self) -> bool:
        if self._ctx is not None:
            return True
        if self._load_failed or not self.is_available():
            return False
        try:
            self._lib = self._open_library()
            self._bind_symbols(self._lib)
            abi = self._lib.parakeet_capi_abi_version()
            logger.info("👂🦜 libparakeet loaded (ABI v%s) from %s", abi, self.lib_path)
            ctx = self._lib.parakeet_capi_load(str(self.model).encode("utf-8"))
            if not ctx:
                logger.error("👂🦜 parakeet_capi_load returned NULL for %s", self.model)
                self._load_failed = True
                return False
            self._ctx = ctx
            return True
        except Exception as exc:
            logger.error("👂🦜 Failed to load libparakeet/model: %s", exc, exc_info=True)
            self._load_failed = True
            return False

    def _open_library(self) -> ctypes.CDLL:
        """Load the shared lib, making its ggml*.dll deps discoverable on Windows."""
        assert self.lib_path is not None
        lib_dir = self.lib_path.parent
        if hasattr(os, "add_dll_directory"):  # Windows / py3.8+
            try:
                os.add_dll_directory(str(lib_dir))
            except OSError:
                pass
            # ggml backend DLLs may sit in sibling dirs under build-shared.
            shared_root = _PK_ROOT / "build-shared"
            if shared_root.is_dir():
                for d in shared_root.rglob("ggml*.dll"):
                    try:
                        os.add_dll_directory(str(d.parent))
                    except OSError:
                        pass
        return ctypes.CDLL(str(self.lib_path))

    @staticmethod
    def _bind_symbols(lib: ctypes.CDLL) -> None:
        lib.parakeet_capi_abi_version.argtypes = []
        lib.parakeet_capi_abi_version.restype = c_int
        lib.parakeet_capi_load.argtypes = [c_char_p]
        lib.parakeet_capi_load.restype = c_void_p
        lib.parakeet_capi_free.argtypes = [c_void_p]
        lib.parakeet_capi_free.restype = None
        # restype c_void_p (NOT c_char_p) so we own the pointer and can free it.
        lib.parakeet_capi_transcribe_pcm.argtypes = [
            c_void_p, POINTER(c_float), c_int, c_int, c_int,
        ]
        lib.parakeet_capi_transcribe_pcm.restype = c_void_p
        lib.parakeet_capi_free_string.argtypes = [c_void_p]
        lib.parakeet_capi_free_string.restype = None
        lib.parakeet_capi_last_error.argtypes = [c_void_p]
        lib.parakeet_capi_last_error.restype = c_char_p

    def _last_error(self) -> str:
        if self._lib is None or self._ctx is None:
            return "(no context)"
        try:
            err = self._lib.parakeet_capi_last_error(self._ctx)
            return err.decode("utf-8", "replace") if err else ""
        except Exception:
            return "(last_error unavailable)"
