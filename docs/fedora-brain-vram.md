# Fedora brain — extend usable memory with GPU VRAM swap (nbd-vram)

> **Status: UNVERIFIED.** These steps run on the **fedora brain box, not on this
> Windows host.** I cannot build, load, or benchmark them from here. Everything
> below is written from the real upstream source of
> [`c0deJedi/nbd-vram`](https://github.com/c0deJedi/nbd-vram) (cloned to
> `E:/Projects/_deps/nbd-vram`), not from a successful run. Treat it as a
> tested-on-paper runbook to execute on fedora and then verify with the commands
> in the last section.

## What this is for

The Clicky persona's brain runs **`qwen2.5-coder` via Ollama on the fedora box
(`moonwolf@192.168.0.108`, Ollama on `:11434`)** — see `jarvis-clicky.bat`. When
you try to run a bigger model (e.g. `qwen2.5-coder:14b` or `:32b`) the limit is
usually **GPU VRAM**: Ollama offloads as many transformer layers to the GPU as
fit, then spills the rest to CPU RAM. If RAM is also tight, the box swaps, and
swapping to a slow SSD makes the model crawl.

`nbd-vram` puts **otherwise-idle VRAM to work as a fast swap device.** On a
hybrid-graphics laptop the display runs off the iGPU while the NVIDIA card sits
idle — its VRAM is free. Even on a desktop, any VRAM Ollama isn't using for the
model can back a swap partition that is far lower-latency than SSD swap for the
sporadic 4 KB page faults that dominate memory pressure (upstream benchmarks:
~335 µs avg vs ~9 ms for power-managed NVMe — 27× faster average latency).

### Important correction to the original premise

The task that scoped this work described nbd-vram as *"a Linux kernel module
exposing GPU VRAM as a block device for swap."* **That is not what it is.** The
upstream README and `install.sh` are explicit:

> "No kernel module to write or maintain. No NVIDIA kernel symbols. Survives
> kernel and driver updates without rebuilding anything."

It is a **userspace daemon** (`nbd-vram.c`, ~one C file, built with
`gcc -ldl`) that:

1. allocates VRAM via the CUDA **driver** API (`cuMemAlloc` / `cuMemcpyHtoD` /
   `cuMemcpyDtoH`) — it only needs `libcuda.so.1`, **not** the full CUDA toolkit;
2. serves that VRAM as a block device over the **NBD** (Network Block Device)
   protocol on a Unix socket;
3. the kernel's **built-in** `nbd` module connects to it and exposes
   `/dev/nbdX`, which is then `mkswap` + `swapon`'d as normal swap.

So the only kernel piece is the stock `nbd` module that ships with virtually
every distro. There is nothing to `insmod`/compile against kernel headers. The
NVIDIA P2P (`nvidia_p2p_get_pages_persistent`) route — the "obvious" kernel
approach — is deliberately **avoided** because it returns `EINVAL` on consumer
GeForce GPUs; the NBD + `cuMemcpy` path works on any CUDA GPU.

## VRAM is a tradeoff for an Ollama box — read this first

VRAM used by `nbd-vram` swap is VRAM **not** available to Ollama for model
layers. The win is only real when one of these holds on the fedora box:

- **Hybrid graphics**: the NVIDIA GPU is *not* driving the display and Ollama
  isn't using all its VRAM, so spare VRAM is genuinely idle.
- **You are RAM-bound, not VRAM-bound**: the bigger model's weights fit in VRAM
  but the box thrashes on system RAM (KV cache, other processes), and you want a
  fast swap tier between RAM and the SSD.

If instead the box is **VRAM-bound** (the model itself won't fit on the GPU),
then handing VRAM to a swap device makes that *worse* — you'd be better off
leaving all VRAM for Ollama and accepting partial CPU offload, or picking a
smaller quant (e.g. `qwen2.5-coder:14b-instruct-q4_K_M`). **Size the swap
allocation to leave Ollama the VRAM it needs for the layers you actually want on
the GPU.** The setup script defaults to a conservative slice and prints the
GPU's free/used VRAM so you can tune it.

Honest expectation: this is a memory-pressure-relief tier (lets a model that
*almost* fits run without falling off a cliff to SSD swap), not a way to make a
32B model fit on an 8 GB card.

## On-fedora steps (run as the box's admin, not from this repo)

A convenience wrapper that performs all of this with VRAM auto-sizing lives at
`scripts/setup-fedora-vram.sh` in this repo — copy it to the fedora box and run
it. The manual equivalent:

```sh
# 1. SSH to the fedora brain box
ssh moonwolf@192.168.0.108

# 2. Prereqs (Fedora): nbd-client + build tools + NVIDIA driver w/ libcuda.so.1.
#    (Fedora uses dnf; the upstream installer assumes Debian/apt for nbd-client,
#     so install it yourself first.)
sudo dnf install -y nbd gcc make
ls /usr/lib64/libcuda.so.1 || echo "NVIDIA driver / libcuda missing — install it first"

# 3. Get the source onto the box and build the daemon
git clone --depth 1 https://github.com/c0deJedi/nbd-vram
cd nbd-vram
make                       # gcc -O2 -Wall -o nbd-vram nbd-vram.c -ldl

# 4. Decide how much VRAM to lend to swap. Check current GPU state:
nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv
#   -> leave Ollama the VRAM it needs; lend only the genuinely-idle remainder.

# 5. Smoke test WITHOUT installing (allocates VRAM, 1 MiB write/readback,
#    activates swap, prints teardown). Safe + reversible.
sudo bash test-nbd.sh

# 6. If the smoke test passes, install the systemd service.
#    Note: install.sh asks about power-aware management (auto-disable on
#    battery) — answer 'N' for an always-on desktop/server.
sudo ./install.sh

# 7. Set the VRAM ceiling + swap priority to your tuned values, then start.
sudo sed -i 's/^Environment=VRAM_SETUP_SIZE_MB=.*/Environment=VRAM_SETUP_SIZE_MB=<MB>/' \
  /etc/systemd/system/vram-swap-nbd.service
sudo systemctl daemon-reload
sudo systemctl start vram-swap-nbd
```

`VRAM_SETUP_SIZE_MB` is a **ceiling, not a hard requirement** — the daemon backs
off in 512 MiB steps if the GPU is short, so it grabs as much as it can even
with the compositor (or Ollama) already loaded.

## Then point Ollama at the bigger model

Once VRAM swap is active and `swapon --show` lists `/dev/nbd0`, pull and run a
larger model on the fedora box:

```sh
# Bigger model now has more headroom (VRAM-backed swap tier under RAM)
ollama pull qwen2.5-coder:14b           # or :32b if the box can take it
# Make sure Ollama is LAN-exposed so Jarvis can reach it from this PC:
sudo systemctl edit ollama              # add: Environment="OLLAMA_HOST=0.0.0.0:11434"
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

Then on the Jarvis (Windows) side, point Clicky's brain at the bigger model.
`jarvis-clicky.bat` hard-codes `MODEL=qwen2.5-coder:7b`; bump it to `:14b`/`:32b`
to match what you pulled (it probes `:11434` for that exact tag before using the
host).

## Verify it's actually working (on fedora)

```sh
# Swap device present at high priority
swapon --show
# NAME       TYPE      SIZE USED PRIO
# /dev/nbd0  partition   7G   0B 1500

# Daemon's own pages are locked into RAM (mlockall) so it can't deadlock under
# its own swap pressure — VmLck non-zero, VmSwap zero:
grep -E 'VmLck|VmSwap' /proc/$(pgrep nbd-vram)/status

# Watch overflow order under load: RAM fills -> VRAM swap absorbs spill -> SSD
# only if exhausted. Run the bigger model and watch:
watch -n1 'free -h; echo; swapon --show; echo; nvidia-smi --query-gpu=memory.used,memory.free --format=csv'
```

To benchmark VRAM swap vs the box's NVMe swap (each restores state on exit):

```sh
cd nbd-vram
sudo bash benchmarks/bench-latency.sh    # the one that matters for swap
```

## Teardown / uninstall (on fedora)

```sh
sudo systemctl stop vram-swap-nbd        # swapoff + disconnect + free VRAM
sudo bash uninstall.sh                   # full removal
```

`systemctl stop` is always respected and reverses cleanly — the VRAM returns to
Ollama immediately.

## Risks / honest caveats

- **Unverified on fedora.** Hardware (GPU model, VRAM size, hybrid vs discrete,
  AC vs battery) is unknown from this host. The `VRAM_SETUP_SIZE_MB` value in
  the script is a placeholder that auto-sizes from `nvidia-smi`; confirm it
  leaves Ollama enough.
- **Upstream is tested on one config** (RTX 3070 Laptop, kernel 6.17, Pop!\_OS —
  a Debian/Ubuntu base). Fedora differs: package manager (`dnf`, not `apt`),
  `libcuda.so.1` path (`/usr/lib64`), and SELinux may need a pass if the daemon
  or `nbd-client` is confined. The upstream `install.sh` shells out to
  `apt-get install nbd-client` — install `nbd` via `dnf` **before** running it.
- **VRAM swap competes with Ollama for VRAM** (see the tradeoff section). This
  helps a RAM-bound box, not a VRAM-bound one.
- **Memory-safety dependency**: the daemon `mlockall`s itself; the systemd unit
  sets `LimitMEMLOCK=infinity`. If you run the daemon by hand without that
  limit, a swap-pressure deadlock is possible. Use the service.
- License: nbd-vram is MIT (Sean Lobjoit / c0deJedi).
