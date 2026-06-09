#!/usr/bin/env bash
# setup-fedora-vram.sh — set up nbd-vram (GPU VRAM as fast swap) on the fedora
# brain box so a bigger Ollama model (qwen2.5-coder:14b/32b) has more headroom.
#
# STATUS: UNVERIFIED. This runs on the fedora brain box (moonwolf@192.168.0.108),
# NOT on the Windows host this repo lives on. It was written from the real
# upstream source of c0deJedi/nbd-vram, not from a successful run. Read
# docs/fedora-brain-vram.md first. Copy this script to fedora and run it there.
#
# nbd-vram is a USERSPACE daemon (CUDA driver API + NBD over a Unix socket),
# NOT a kernel module — the only kernel piece is the stock built-in `nbd`
# module. Nothing is compiled against kernel headers.
#
# What it does:
#   1. Sanity-checks the host (root, libcuda, nvidia-smi, nbd-client, gcc).
#   2. Clones + builds the nbd-vram daemon.
#   3. Auto-sizes the VRAM-for-swap slice from nvidia-smi free VRAM (leaving
#      Ollama headroom), unless VRAM_MB is set in the environment.
#   4. Runs the upstream smoke test (reversible) unless --no-test.
#   5. With --install, installs the systemd service and starts it.
#
# Usage (on fedora):
#   chmod +x setup-fedora-vram.sh
#   sudo ./setup-fedora-vram.sh                 # build + smoke test only (safe)
#   sudo VRAM_MB=4096 ./setup-fedora-vram.sh --install   # install service too
#
# Env:
#   VRAM_MB       VRAM (MiB) to lend to swap. Default: auto (free_VRAM - HEADROOM).
#   HEADROOM_MB   VRAM (MiB) to leave for Ollama/display when auto-sizing. Default 2048.
#   PRIORITY      swap priority (higher = used before SSD swap). Default 1500.
#   SRC_DIR       existing nbd-vram checkout to reuse instead of cloning.
#
# License: nbd-vram is MIT (c0deJedi). This wrapper is part of jarvis-class-interface.

set -euo pipefail

REPO_URL="https://github.com/c0deJedi/nbd-vram"
HEADROOM_MB="${HEADROOM_MB:-2048}"
PRIORITY="${PRIORITY:-1500}"
DO_INSTALL=0
DO_TEST=1

log()  { printf '\033[36m[vram-setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[vram-setup] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[vram-setup] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --install)  DO_INSTALL=1 ;;
    --no-test)  DO_TEST=0 ;;
    -h|--help)
      sed -n '2,40p' "$0"; exit 0 ;;
    *) die "unknown arg: $arg (try --help)" ;;
  esac
done

# ── 1. Sanity checks ──────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "must run as root (sudo). nbd/swap need privileges."

log "Checking host prerequisites..."
if ! ls /usr/lib64/libcuda.so.1 /usr/lib/x86_64-linux-gnu/libcuda.so.1 >/dev/null 2>&1; then
  warn "libcuda.so.1 not found in the usual paths — is the NVIDIA driver installed?"
  warn "nbd-vram needs only the driver (libcuda), not the full CUDA toolkit."
fi
command -v nvidia-smi >/dev/null 2>&1 || die "nvidia-smi not found — NVIDIA driver required."
command -v gcc        >/dev/null 2>&1 || die "gcc not found — install: sudo dnf install -y gcc make"

if ! command -v nbd-client >/dev/null 2>&1; then
  warn "nbd-client not found. Attempting install..."
  if   command -v dnf     >/dev/null 2>&1; then dnf install -y nbd
  elif command -v apt-get >/dev/null 2>&1; then apt-get install -y nbd-client
  else die "no dnf/apt — install nbd-client manually."; fi
fi

# Make sure the stock nbd kernel module is loadable (this is the ONLY kernel bit).
modprobe nbd max_part=0 2>/dev/null || warn "could not modprobe nbd — check it's built into the kernel."

# ── 2. Source + build ─────────────────────────────────────────────────────
if [ -n "${SRC_DIR:-}" ]; then
  [ -d "$SRC_DIR" ] || die "SRC_DIR=$SRC_DIR does not exist."
  log "Reusing existing checkout: $SRC_DIR"
else
  SRC_DIR="$(mktemp -d /tmp/nbd-vram.XXXXXX)"
  log "Cloning nbd-vram -> $SRC_DIR"
  git clone --depth 1 "$REPO_URL" "$SRC_DIR"
fi
cd "$SRC_DIR"

log "Building daemon (gcc -ldl)..."
make
[ -x ./nbd-vram ] || die "build did not produce ./nbd-vram"

# ── 3. Auto-size the VRAM slice ───────────────────────────────────────────
free_mb="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
total_mb="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
log "GPU VRAM: total=${total_mb} MiB, free=${free_mb} MiB"

if [ -n "${VRAM_MB:-}" ]; then
  size_mb="$VRAM_MB"
  log "Using VRAM_MB from env: ${size_mb} MiB"
else
  size_mb=$(( free_mb - HEADROOM_MB ))
  [ "$size_mb" -ge 512 ] || die "Only $((free_mb)) MiB free; after ${HEADROOM_MB} MiB headroom there's < 512 MiB to lend.
This box looks VRAM-bound — leave VRAM for Ollama and pick a smaller model/quant instead (see docs/fedora-brain-vram.md)."
  # round down to a 512 MiB step (the daemon allocates in 512 MiB units)
  size_mb=$(( (size_mb / 512) * 512 ))
  log "Auto-sized VRAM-for-swap: ${size_mb} MiB (free ${free_mb} - headroom ${HEADROOM_MB}, rounded to 512 MiB step)"
fi

# ── 4. Smoke test (reversible) ────────────────────────────────────────────
if [ "$DO_TEST" -eq 1 ]; then
  log "Running upstream smoke test (allocates VRAM, 1 MiB write/readback, swapon, then prints teardown)..."
  if VRAM_SETUP_SIZE_MB="$size_mb" bash test-nbd.sh; then
    log "Smoke test passed."
  else
    die "Smoke test failed — do NOT install. Inspect output above (common: SELinux confinement, GSP RPC timeout on laptops, insufficient free VRAM)."
  fi
fi

# ── 5. Install service ────────────────────────────────────────────────────
if [ "$DO_INSTALL" -eq 1 ]; then
  log "Installing systemd service via upstream install.sh..."
  warn "install.sh will ask about power-aware management — answer 'N' for an always-on server/desktop."
  ./install.sh

  unit=/etc/systemd/system/vram-swap-nbd.service
  if [ -f "$unit" ]; then
    log "Setting VRAM_SETUP_SIZE_MB=${size_mb}, VRAM_SWAP_PRIORITY=${PRIORITY} in the unit..."
    sed -i "s/^Environment=VRAM_SETUP_SIZE_MB=.*/Environment=VRAM_SETUP_SIZE_MB=${size_mb}/" "$unit"
    sed -i "s/^Environment=VRAM_SWAP_PRIORITY=.*/Environment=VRAM_SWAP_PRIORITY=${PRIORITY}/" "$unit"
    systemctl daemon-reload
    systemctl start vram-swap-nbd
    log "Started vram-swap-nbd. Verifying..."
    sleep 2
    swapon --show || true
    grep -E 'VmLck|VmSwap' "/proc/$(pgrep -x nbd-vram | head -n1)/status" 2>/dev/null || \
      warn "could not read daemon /proc status — check 'systemctl status vram-swap-nbd'."
  else
    warn "expected unit $unit not found after install.sh — verify the install."
  fi

  cat <<EOF

[vram-setup] Done. Next:
  1. ollama pull qwen2.5-coder:14b     # or :32b, on THIS box
  2. Expose Ollama to the LAN so Jarvis can reach it:
       sudo systemctl edit ollama      # add: Environment="OLLAMA_HOST=0.0.0.0:11434"
       sudo systemctl daemon-reload && sudo systemctl restart ollama
  3. On the Windows/Jarvis side, bump MODEL in jarvis-clicky.bat to the tag you pulled.

  Verify swap tier:  swapon --show   (look for /dev/nbd0 at priority ${PRIORITY})
  Stop/undo:         sudo systemctl stop vram-swap-nbd   (frees VRAM back to Ollama)
EOF
else
  cat <<EOF

[vram-setup] Build + smoke test complete (service NOT installed).
  Re-run with --install to install the systemd service and start it:
    sudo VRAM_MB=${size_mb} $0 --install
EOF
fi
