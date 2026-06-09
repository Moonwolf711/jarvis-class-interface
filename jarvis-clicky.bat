@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  JARVIS — "Clicky" persona, brain = qwen2.5-coder:7b on the
REM  best available LAN Ollama. STT/TTS still ElevenLabs.
REM  Auto-picks: fedora (moonwolf laptop) first, then TheHAVEN.
REM  Mirrors haven_run.bat; only the brain location/model differ.
REM ============================================================

set "MODEL=qwen2.5:14b"
REM Candidate Ollama hosts, in preference order (this box is on 192.168.0.x):
REM   192.168.0.108  -> ssh "fedora", user moonwolf  (the moonwolf laptop)
REM   192.168.0.83   -> ssh "haven", RTX 3080        (always-on GPU fallback)
REM   NOT 192.168.1.180 — that's the Raspberry Pi "moonpie" (ARM, no GPU).
set "HOSTS=192.168.0.108 192.168.0.83"

set "OLLAMA_HOST_PICKED="
for %%H in (%HOSTS%) do (
  if not defined OLLAMA_HOST_PICKED (
    echo Probing %%H:11434 for %MODEL% ...
    curl -s -m 5 "http://%%H:11434/api/tags" | findstr /C:"%MODEL%" >nul 2>&1
    if not errorlevel 1 (
      set "OLLAMA_HOST_PICKED=%%H"
      echo   -> using %%H
    )
  )
)

if not defined OLLAMA_HOST_PICKED (
  echo.
  echo [BLOCKER] %MODEL% not reachable on any candidate host (%HOSTS%).
  echo   1^) This PC's LAN may be walled off by the Brave-VPN kill-switch.
  echo      Toggle Brave VPN OFF (or allow local network), then re-run.
  echo   2^) On the target box, expose Ollama to the LAN and pull the model:
  echo        Linux/systemd:  sudo systemctl edit ollama  -^>  Environment="OLLAMA_HOST=0.0.0.0:11434"
  echo                        sudo systemctl daemon-reload ^&^& sudo systemctl restart ollama
  echo                        ollama pull %MODEL%
  echo        Windows:        setx OLLAMA_HOST 0.0.0.0:11434  ^&^&  ollama pull %MODEL%
  pause
  exit /b 1
)

cd /d C:\Users\Owner\jarvis-class-interface\code || exit /b 1
call ..\venv\Scripts\activate.bat

REM Brain wiring — env (provider/base_url/model) belt-and-suspenders with the
REM Clicky persona's own model field, so it's correct either way it's routed.
set "LLM_PROVIDER=ollama"
set "LLM_MODEL=%MODEL%"
set "OLLAMA_BASE_URL=http://%OLLAMA_HOST_PICKED%:11434"

REM HTTPS (self-signed) so phone browsers grant mic access on the LAN.
set "USE_SSL=1"
set "SSL_CERTFILE=jarvis-haven.pem"
set "SSL_KEYFILE=jarvis-haven-key.pem"

echo Starting JARVIS (Clicky persona, brain: %MODEL% @ %OLLAMA_HOST_PICKED%) ...
python server.py
endlocal
