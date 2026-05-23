"""Generate ARC-reactor Jarvis icon. Renders supersampled and saves multi-res ICO."""
from PIL import Image, ImageDraw, ImageFilter
from pathlib import Path

OUT_DIR = Path(__file__).parent
SS = 4  # supersample factor for anti-aliasing
SIZE = 256 * SS
CENTER = (SIZE // 2, SIZE // 2)

VOID = (5, 8, 16, 255)
CYAN = (20, 227, 255)
CYAN_BRIGHT = (106, 255, 255)
EMBER = (255, 107, 26)


def rgba(rgb, a):
    return (*rgb, max(0, min(255, int(a))))


def circle(draw, cx, cy, r, fill=None, outline=None, width=1):
    box = (cx - r, cy - r, cx + r, cy + r)
    if fill is not None:
        draw.ellipse(box, fill=fill)
    if outline is not None:
        draw.ellipse(box, outline=outline, width=width)


def main():
    img = Image.new("RGBA", (SIZE, SIZE), VOID)
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)

    cx, cy = CENTER
    r_outer = SIZE * 0.46
    r_mid = SIZE * 0.36
    r_inner = SIZE * 0.26
    r_core = SIZE * 0.18

    # Outer aura — concentric soft rings
    for i in range(60, 0, -1):
        rr = r_outer + (i * 4)
        alpha = max(0, 40 - i // 2)
        circle(gd, cx, cy, rr, outline=rgba(CYAN, alpha), width=SS * 2)

    # Hard outer ring (broken)
    for ang_start in (0, 100, 180, 280):
        # arcs at 0, 100, 180, 280 degrees, 30 deg long
        gd.arc(
            (cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer),
            ang_start,
            ang_start + 30,
            fill=rgba(CYAN_BRIGHT, 255),
            width=int(SS * 1.5),
        )

    # Mid ring — dashed
    for ang in range(0, 360, 8):
        gd.arc(
            (cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid),
            ang,
            ang + 3,
            fill=rgba(CYAN, 150),
            width=SS,
        )

    # Inner accent — partial ring (top-right) with ember sliver opposite
    gd.arc(
        (cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner),
        300,
        420,
        fill=rgba(CYAN_BRIGHT, 230),
        width=int(SS * 2.5),
    )
    gd.arc(
        (cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner),
        120,
        240,
        fill=rgba(EMBER, 200),
        width=int(SS * 1.5),
    )

    # Core radial gradient — circles from outer dim to inner bright
    for i in range(int(r_core) * 2, 0, -1):
        t = 1 - (i / (r_core * 2))  # 0 at edge, 1 at center
        # blend void → cyan_bright
        rr = i / 2
        a = int(255 * (t ** 1.5))
        circle(gd, cx, cy, rr, fill=rgba(
            (
                int(VOID[0] + (CYAN_BRIGHT[0] - VOID[0]) * t),
                int(VOID[1] + (CYAN_BRIGHT[1] - VOID[1]) * t),
                int(VOID[2] + (CYAN_BRIGHT[2] - VOID[2]) * t),
            ),
            a,
        ))

    # Subtle reticle crosshair
    line_alpha = 60
    gd.line((cx - r_outer * 1.05, cy, cx - r_inner * 0.6, cy), fill=rgba(CYAN, line_alpha), width=SS)
    gd.line((cx + r_inner * 0.6, cy, cx + r_outer * 1.05, cy), fill=rgba(CYAN, line_alpha), width=SS)
    gd.line((cx, cy - r_outer * 1.05, cx, cy - r_inner * 0.6), fill=rgba(CYAN, line_alpha), width=SS)
    gd.line((cx, cy + r_inner * 0.6, cx, cy + r_outer * 1.05), fill=rgba(CYAN, line_alpha), width=SS)

    # Composite glow with a blur for halo
    halo = glow.filter(ImageFilter.GaussianBlur(SIZE * 0.012))
    img = Image.alpha_composite(img, halo)
    img = Image.alpha_composite(img, glow)

    # Downsample to target sizes
    sizes = [256, 128, 64, 48, 32, 16]
    out_imgs = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    ico_path = OUT_DIR / "jarvis.ico"
    out_imgs[0].save(ico_path, format="ICO", sizes=[(s, s) for s in sizes])
    out_imgs[0].save(OUT_DIR / "jarvis.png")
    print(f"wrote {ico_path}")
    print(f"wrote {OUT_DIR / 'jarvis.png'}")


if __name__ == "__main__":
    main()
