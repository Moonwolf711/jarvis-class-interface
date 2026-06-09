<#
.SYNOPSIS
    Download a Parakeet GGUF model for the optional Parakeet STT backend.

.DESCRIPTION
    Pulls one .gguf from the HuggingFace collection mudler/parakeet-cpp-gguf into
    third_party/parakeet.cpp/models/ and prints the PARAKEET_MODEL line to set.

    Pick by tradeoff:
      * parakeet-tdt_ctc-110m  -- smallest/fastest, English. Best for a CPU speed test.
      * parakeet-tdt-0.6b-v2   -- higher accuracy, English (default below).
      * parakeet-tdt-0.6b-v3   -- multilingual (25 EU langs).
    dtypes: f16 / q8_0 (near-lossless) / q6_k / q5_k / q4_k (smaller, small WER cost).

    The exact filenames live in the repo's file list -- VERIFY before trusting the
    default, naming can differ slightly:
        https://huggingface.co/mudler/parakeet-cpp-gguf/tree/main

.PARAMETER File
    GGUF filename within the HF repo (default: parakeet-tdt-0.6b-v2-q8_0.gguf).

.EXAMPLE
    pwsh scripts/fetch_parakeet_model.ps1
    pwsh scripts/fetch_parakeet_model.ps1 -File parakeet-tdt_ctc-110m-q5_k.gguf
#>
param([string]$File = "parakeet-tdt-0.6b-v2-q8_0.gguf")

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$models = Join-Path $repo "third_party\parakeet.cpp\models"
New-Item -ItemType Directory -Force -Path $models | Out-Null
$dest = Join-Path $models $File
$hfRepo = "mudler/parakeet-cpp-gguf"

if (Test-Path $dest) {
    Write-Host "==> Already present: $dest" -ForegroundColor Green
} else {
    $hf = Get-Command huggingface-cli -ErrorAction SilentlyContinue
    if ($hf) {
        Write-Host "==> Downloading $File via huggingface-cli..." -ForegroundColor Cyan
        huggingface-cli download $hfRepo $File --local-dir $models
    } else {
        $url = "https://huggingface.co/$hfRepo/resolve/main/$File"
        Write-Host "==> huggingface-cli not found; downloading via HTTP:`n    $url" -ForegroundColor Cyan
        Write-Warning "If this 404s, the filename is wrong -- check https://huggingface.co/$hfRepo/tree/main and re-run with -File <name>."
        Invoke-WebRequest -Uri $url -OutFile $dest
    }
}

if (Test-Path $dest) {
    Write-Host "`n==> Model: $dest" -ForegroundColor Green
    Write-Host "Enable the backend in Jarvis with:" -ForegroundColor Green
    Write-Host "    `$env:STT_FINAL_ENGINE = `"parakeet`"" -ForegroundColor Gray
    Write-Host "    `$env:PARAKEET_MODEL   = `"$dest`"" -ForegroundColor Gray
}
