<#
.SYNOPSIS
    Build the vendored parakeet.cpp CLI (third_party/parakeet.cpp) for the
    optional Parakeet final-transcription backend in code/stt_parakeet.py.

.DESCRIPTION
    Inits the ggml submodule, configures CMake, and builds parakeet-cli.

    SCOPE NOTE — this builds the SUBPROCESS path only. The subprocess CLI reloads
    the GGUF model on every call, so it is a TRANSCRIPT-QUALITY PROBE, not a
    real-time latency path. The latency-viable production path is the parakeet.cpp
    C-API (load model once, keep ctx warm) via ctypes against libparakeet — build
    that with  -DPARAKEET_SHARED=ON  and bind include/parakeet_capi.h. That FFI
    binding is intentionally NOT scaffolded yet (phase 2).

    Toolchain on Windows:
      * MSVC (Visual Studio Build Tools) is the smooth path and is REQUIRED for
        the CUDA backend (-DPARAKEET_GGML_CUDA=ON needs cl.exe + CUDA Toolkit).
      * MinGW may build the CPU path but is not the parakeet.cpp/ggml default.

.PARAMETER Cuda
    Build the CUDA GPU backend (requires MSVC + NVIDIA CUDA Toolkit).

.EXAMPLE
    pwsh scripts/build_parakeet.ps1
    pwsh scripts/build_parakeet.ps1 -Cuda
#>
param([switch]$Cuda)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$pk = Join-Path $repo "third_party\parakeet.cpp"

if (-not (Test-Path $pk)) {
    Write-Host "==> parakeet.cpp not vendored; cloning (recursive)..." -ForegroundColor Cyan
    git clone --recursive https://github.com/mudler/parakeet.cpp $pk
} else {
    Write-Host "==> Initializing ggml submodule..." -ForegroundColor Cyan
    git -C $pk submodule update --init --recursive
}

$flags = @("-DPARAKEET_BUILD_CLI=ON")
if ($Cuda) {
    Write-Host "==> CUDA backend requested (needs MSVC + CUDA Toolkit)." -ForegroundColor Yellow
    $flags += "-DPARAKEET_GGML_CUDA=ON"
}

Write-Host "==> Configuring CMake..." -ForegroundColor Cyan
cmake -B "$pk\build" -S $pk @flags

Write-Host "==> Building (Release)..." -ForegroundColor Cyan
cmake --build "$pk\build" --config Release -j

$bin = Join-Path $pk "build\examples\cli\parakeet-cli.exe"
if (-not (Test-Path $bin)) {
    # Some generators nest the Release config one level deeper.
    $alt = Join-Path $pk "build\examples\cli\Release\parakeet-cli.exe"
    if (Test-Path $alt) { $bin = $alt }
}

if (Test-Path $bin) {
    Write-Host "`n==> Built: $bin" -ForegroundColor Green
    Write-Host "Set this in Jarvis if it is not the default location:" -ForegroundColor Green
    Write-Host "    `$env:PARAKEET_BIN = `"$bin`"" -ForegroundColor Gray
    Write-Host "Next: fetch a model -> scripts/fetch_parakeet_model.ps1" -ForegroundColor Green
} else {
    Write-Warning "Build finished but parakeet-cli.exe was not found under build/examples/cli. Check CMake output above."
}
