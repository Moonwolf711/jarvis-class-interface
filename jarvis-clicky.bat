@echo off
REM ============================================================
REM  JARVIS — "Clicky" persona, brain = qwen2.5-coder:7b on the
REM  moonwolf laptop over LAN (Ollama). STT/TTS still ElevenLabs.
REM  Mirrors haven_run.bat; only the brain location/model differ.
REM ============================================================

REM --- EDIT THIS to the moonwolf laptop's LAN IP ---------------
REM   Candidates found in ~/.ssh/config (this box is on 192.168.0.x):
REM     192.168.0.108  -> ssh host "fedora", user moonwolf   <-- best name match
REM     192.168.0.7    -> ssh host "laptop", user tyler
REM   NOT 192.168.1.180 — that's the Raspberry Pi "moonpie" (ARM, no GPU).
set "MOONWOLF_IP=192.168.0.108"
REM ------------------------------------------------------------

cd /d C:\Users\Owner\jarvis-class-interface\code || exit /b 1
call ..\venv\Scripts\activate.bat

REM Brain = local qwen2.5-coder:7b on the moonwolf laptop's GPU via Ollama.
REM Set BOTH the env (provider/base_url/model) AND the Clicky persona's model
REM field, so it's correct whether routed by env or by persona.
set "LLM_PROVIDER=ollama"
set "LLM_MODEL=qwen2.5-coder:7b"
set "OLLAMA_BASE_URL=http://%MOONWOLF_IP%:11434"

REM Preflight: confirm the remote Ollama is reachable and has the model.
echo Checking Ollama at %OLLAMA_BASE_URL% ...
curl -s -m 5 "%OLLAMA_BASE_URL%/api/tags" | findstr /C:"qwen2.5-coder:7b" >nul
if errorlevel 1 (
  echo [BLOCKER] qwen2.5-coder:7b not reachable at %OLLAMA_BASE_URL%
  echo   On the moonwolf laptop, run once:
  echo     setx OLLAMA_HOST 0.0.0.0:11434   ^&^&  ollama pull qwen2.5-coder:7b
  echo   then restart Ollama so it binds to the LAN, and re-run this script.
  pause
  exit /b 1
)
echo Ollama OK — qwen2.5-coder:7b is available.

REM HTTPS (self-signed) so phone browsers grant mic access on the LAN.
set "USE_SSL=1"
set "SSL_CERTFILE=jarvis-haven.pem"
set "SSL_KEYFILE=jarvis-haven-key.pem"

echo Starting JARVIS (Clicky persona, brain: qwen2.5-coder:7b @ %MOONWOLF_IP%) ...
python server.py
