"""Oscar pixel-art logo pipeline: photo -> 16/32-bit style sprite + export set.

Run with system python3 (PIL, numpy, scipy). Writes to OUT_DIR.
"""
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from scipy import ndimage as ndi

SRC = "/Users/axelehrnrooth/Desktop/Oscar AI/Oscar-Martinez.jpg"
OUT_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "out")

HS = (115, 20, 775, 680)     # head & shoulders square crop (x0, y0, x1, y1)
FACE = (262, 78, 642, 458)   # tight face crop: just above hairline -> chin


# ---------------------------------------------------------------- source prep
def build_mask(rgb):
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    barrier = (b - r > 45) & (b > 160) & (g > 140)  # the light-blue cutout outline
    # seal JPEG gaps; pad first so closing doesn't erode the outline where it meets the frame
    barrier = ndi.binary_closing(np.pad(barrier, 4, mode="edge"), iterations=2)[4:-4, 4:-4]
    barrier = ndi.binary_dilation(barrier, iterations=1)
    lab, _ = ndi.label(~barrier)
    h, w = barrier.shape
    face = lab[h * 2 // 5, w * 9 // 20]  # a face pixel: its region is never "outside"
    seeds = {lab[y, x] for y, x in [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]} - {0, face}
    inside = ~np.isin(lab, list(seeds)) & ~barrier
    lab2, n = ndi.label(inside)
    inside = lab2 == 1 + int(np.argmax(ndi.sum(inside, lab2, range(1, n + 1))))
    return ndi.binary_fill_holes(inside)


def load_source():
    rgb = np.array(Image.open(SRC).convert("RGB"))
    inside = build_mask(rgb)
    # erode ~14px off the outline (not along the bottom frame edge, where the body is cut)
    pad = np.pad(inside, ((0, 20), (0, 0)), mode="edge")
    core = ndi.binary_erosion(pad, np.ones((3, 3)), iterations=14)[: inside.shape[0]]
    _, (iy, ix) = ndi.distance_transform_edt(~core, return_indices=True)
    return rgb, inside, rgb[iy, ix]  # filled: outside pixels take the nearest inside colour


RGB, INSIDE, FILLED = load_source()


def crop(a, box):
    x0, y0, x1, y1 = box
    return a[y0:y1, x0:x1]


# ---------------------------------------------------------------- colour maths
def to_lab(rgb):
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.9505, 1.0, 1.089])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def luma(rgb):
    rgb = np.asarray(rgb, float)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def kmeans(px, k, iters=40, seed=1):
    lab = to_lab(px)
    rng = np.random.default_rng(seed)
    # init: spread along lightness so dark/light extremes both get a centre
    order = np.argsort(lab[:, 0])
    c = lab[order[np.linspace(0, len(px) - 1, k).astype(int)]].copy()
    for _ in range(iters):
        a = ((lab[:, None] - c[None]) ** 2).sum(-1).argmin(1)
        for i in range(k):
            if (a == i).any():
                c[i] = lab[a == i].mean(0)
    out = np.array([px[a == i].mean(0) if (a == i).any() else px[rng.integers(len(px))] for i in range(k)])
    return np.round(out).astype(np.uint8)


# ---------------------------------------------------------------- continuous prep
def prep(box, size, contrast=1.3, sat=1.05, usm=(1.0, 70, 2), detail=0.0):
    src = Image.fromarray(crop(FILLED, box))
    im = src.resize((size, size), Image.LANCZOS)
    im = ImageEnhance.Contrast(im).enhance(contrast)
    im = ImageEnhance.Color(im).enhance(sat)
    im = im.filter(ImageFilter.UnsharpMask(radius=usm[0], percent=usm[1], threshold=usm[2]))
    a = np.array(im).astype(float)
    if detail:  # extra local contrast so features survive at small sizes
        a = a + detail * (a - ndi.gaussian_filter(a, (size / 40, size / 40, 0)))
    alpha = np.array(Image.fromarray((crop(INSIDE, box) * 255).astype(np.uint8)).resize((size, size), Image.BOX)) >= 128
    return np.clip(a, 0, 255), alpha


REGIONS = ["hair", "skin", "shirt", "tie"]


def regions(a, alpha, box):
    """Label each pixel hair/skin/shirt/tie from colour + position in the source frame."""
    size = a.shape[0]
    x0, y0, x1, y1 = box
    ys = y0 + (np.arange(size)[:, None] + 0.5) * (y1 - y0) / size  # source-y of each row
    xs = x0 + (np.arange(size)[None, :] + 0.5) * (x1 - x0) / size
    ys, xs = np.broadcast_to(ys, (size, size)), np.broadcast_to(xs, (size, size))
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    L = luma(a)
    mx, mn = a.max(-1), a.min(-1)
    s = (mx - mn) / np.maximum(mx, 1)
    lab = np.full((size, size), -1)
    body = ys > 395 + 0.25 * np.abs(xs - 445)       # below the neckline / shoulders
    tie = body & (b > r - 8) & (L < 125) & (np.abs(xs - 445) < 75) & (ys > 470)
    hair = ~body & (L < 62) & (ys < 330) | ~body & (L < 45) & (ys < 200)
    shirt = body & ~tie & (s < 0.36)
    skin = ~hair & ~tie & ~shirt & alpha
    sl, n = ndi.label(skin)
    if n:
        sizes = ndi.sum(skin, sl, range(1, n + 1))
        small = np.isin(sl, 1 + np.nonzero(sizes < max(6, size * size / 600))[0]) & body
        skin &= ~small
        shirt |= small & ~tie
    for i, m in enumerate([hair, skin, shirt, tie]):
        lab[m & alpha] = i
    return lab


# ---------------------------------------------------------------- palette
QUOTA = {"hair": 3, "skin": 7, "shirt": 5}
EXTRA = {  # hand-picked accents, all checked against the photo
    # tie: photo is slate (#302c3a) with a paler pattern; pushed toward navy so it reads at sprite size
    "tie0": (24, 24, 46),
    "tie1": (40, 42, 78),
    "tie2": (62, 68, 116),
    "tie3": (128, 140, 190),
    "lips": (150, 64, 56),       # photo lip (148,74,69) after the contrast boost
    "ink": (14, 9, 14),          # outline / pupils
    "skin_ink": (66, 30, 20),    # outline on skin
    "shirt_ink": (98, 78, 84),   # outline on shirt + stripe shadow
    "sky": (142, 197, 227),      # the photo's cutout blue -> brand / tile colour
    "deep": (30, 52, 92),        # dark tile / banner colour
}


def build_palette(a, reg, raw):
    pal, names = [], []
    sm = smooth_skin(a, reg)
    mx, mn = sm.max(-1), sm.min(-1)
    skin_ok = (reg == 1) & ((mx - mn) / np.maximum(mx, 1) > 0.36)  # drop collar/shirt contamination
    for i, name in enumerate(REGIONS):
        if name not in QUOTA:
            continue
        sel = skin_ok if name == "skin" else reg == i
        cs = kmeans((sm if name == "skin" else a)[sel], QUOTA[name])
        cs = cs[np.argsort(luma(cs))]
        for j, c in enumerate(cs):
            pal.append(tuple(int(v) for v in c)); names.append(f"{name}{j}")
    for n, c in EXTRA.items():
        pal.append(c); names.append(n)
    return np.array(pal, np.uint8), names


# ---------------------------------------------------------------- render
def smooth_skin(a, reg, sigma=0.9):
    """Blur skin only (normalised inside the skin mask) so shading quantizes into clean clusters."""
    m = (reg == 1).astype(float)
    num = ndi.gaussian_filter(a * m[..., None], (sigma, sigma, 0))
    den = ndi.gaussian_filter(m, sigma)[..., None]
    out = a.copy()
    out[reg == 1] = (num / np.maximum(den, 1e-6))[reg == 1]
    return out


def raw_small(box, size):
    return np.array(Image.fromarray(crop(FILLED, box)).resize((size, size), Image.BOX)).astype(float)


def nearest(a_lab, pal_lab, idx):
    d = ((a_lab[..., None, :] - pal_lab[idx][None]) ** 2).sum(-1)
    o = np.argsort(d, -1)
    return np.array(idx)[o[..., 0]], np.array(idx)[o[..., 1]], np.take_along_axis(d, o[..., :2], -1)


def majority(lab, mask, protect, passes=1):
    """3x3 majority vote inside `mask` -> fewer, cleaner clusters."""
    for _ in range(passes):
        p = np.pad(lab, 1, mode="edge")
        h, w = lab.shape
        stack = np.stack([p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)], -1)
        out = lab.copy()
        for y, x in zip(*np.nonzero(mask & ~protect)):
            c = Counter(stack[y, x].tolist()).most_common(1)[0]
            if c[1] >= 5:
                out[y, x] = c[0]
        lab = out
    return lab


def remove_orphans(lab, mask, protect):
    h, w = lab.shape
    p = np.pad(lab, 1, constant_values=-9)
    for y, x in zip(*np.nonzero(mask & ~protect)):
        nb = [p[y + dy, x + dx] for dy in range(3) for dx in range(3) if (dy, dx) != (1, 1)]
        if lab[y, x] not in nb:
            nb = [v for v in nb if v >= 0]
            if nb:
                lab[y, x] = Counter(nb).most_common(1)[0][0]
    return lab


def render(box, size, pal, names, opts):
    a, alpha = prep(box, size, **opts.get("prep", {}))
    reg = regions(a, alpha, box)
    pal_lab = to_lab(pal.astype(float))
    alab = to_lab(smooth_skin(a, reg, opts.get("skin_sigma", 0.9)))
    rawlab = to_lab(raw_small(box, size))
    N = {n: i for i, n in enumerate(names)}
    lab = np.full((size, size), -1)
    protect = np.zeros((size, size), bool)
    L = luma(a)
    for ri, rname in enumerate(REGIONS):
        idx = [N[n] for n in names if n.startswith(rname)]
        if rname == "hair":
            idx.append(N["ink"])
        if rname == "skin":
            idx.append(N["lips"])
        m = reg == ri
        p1, p2, d = nearest(rawlab if rname == "tie" else alab, pal_lab, idx)
        sel = p1
        if opts.get("dither", True) and rname in ("skin", "shirt", "hair"):
            # light checker dither only in shadows, only where the value sits between two tones
            t = np.sqrt(d[..., 0]) / (np.sqrt(d[..., 0]) + np.sqrt(d[..., 1]) + 1e-6)
            shadow = L < np.percentile(L[m], 35) if m.any() else np.zeros_like(m)
            yy, xx = np.indices(lab.shape)
            dith = shadow & (t > 0.42) & ((xx + yy) % 2 == 0)
            sel = np.where(dith, p2, p1)
        lab[m] = sel[m]
    # features (darkest face pixels) are protected from cleanup
    face = reg == 1
    feat = face & (L < np.percentile(L[face], opts.get("feat_pct", 6)))
    protect |= feat
    lab = majority(lab, alpha & ~feat, protect, opts.get("majority", 1))
    lab = stylise_hair(lab, reg, a, N, opts)
    if opts.get("stripes", True):
        lab = stylise_shirt(lab, reg, a, N, opts)
        lab = stylise_tie(lab, reg, N)
    lab = remove_orphans(lab, alpha, protect | (reg == 3))
    lab = outline(lab, alpha, reg, N)
    before = lab.copy()
    lab = apply_strokes(lab, opts.get("strokes", []), N)
    tuned = before != lab
    edge = alpha & ~ndi.binary_erosion(alpha, structure=[[0, 1, 0], [1, 1, 1], [0, 1, 0]], border_value=1)
    lab = remove_orphans(lab, alpha, tuned | (reg == 3) | edge)
    return lab


def bresenham(x0, y0, x1, y1):
    pts, dx, dy = [], abs(x1 - x0), -abs(y1 - y0)
    sx, sy, err = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1), abs(x1 - x0) - abs(y1 - y0)
    while True:
        pts.append((x0, y0))
        if (x0, y0) == (x1, y1):
            return pts
        e2 = 2 * err
        if e2 >= dy:
            err += dy; x0 += sx
        if e2 <= dx:
            err += dx; y0 += sy


def apply_strokes(lab, strokes, N):
    """Hand-tuning layer. Each stroke: (kind, geometry, colour-name).
    kinds: 'px' [(x,y)...] | 'line' (x0,y0,x1,y1) | 'h' (x0,x1,y) | 'poly' [(x,y)...] (filled)
    | 'polystripe' ([(x,y)...], base, stripe, period, (dx,dy)) -> filled with 1px diagonal stripes"""
    h, w = lab.shape
    names = sorted(N, key=N.get)
    plain_skin = [N[n] for n in names if n.startswith("skin") and n[4:].isdigit() and int(n[4:]) >= 2]
    for kind, g, n in strokes:
        if kind == "map":  # full hand-painted pixel map; legend maps char -> colour name, '?' keeps, '.' clears
            legend, rows = g
            for y, row in enumerate(rows):
                for x, ch in enumerate(row.replace(" ", "")):
                    if ch == "?":
                        continue
                    lab[y, x] = -1 if ch == "." else N[legend[ch]]
            continue
        if kind == "clear":  # rect -> dominant plain skin tone of the surrounding 7x7
            x0, y0, x1, y1 = g
            src = lab.copy()
            for y in range(y0, y1 + 1):
                for x in range(x0, x1 + 1):
                    win = src[max(0, y - 3):y + 4, max(0, x - 3):x + 4].ravel()
                    win = [v for v in win if v in plain_skin]
                    if win and src[y, x] >= 0:
                        lab[y, x] = Counter(win).most_common(1)[0][0]
            continue
        if kind == "polystripe":
            pts, base, stripe, period, (ddx, ddy) = g
            m = Image.new("L", (w, h)); ImageDraw.Draw(m).polygon(pts, fill=1)
            m = np.array(m).astype(bool) & (lab >= 0)
            yy, xx = np.indices(lab.shape)
            on = ((xx * ddy - yy * ddx) % period) == 0
            lab[m] = N[base]; lab[m & on] = N[stripe]
            continue
        if kind == "poly":
            m = Image.new("L", (w, h)); ImageDraw.Draw(m).polygon(g, fill=1)
            lab[np.array(m).astype(bool) & (lab >= 0)] = N[n]
            continue
        pts = {"px": lambda: g, "line": lambda: bresenham(*g), "h": lambda: [(x, g[2]) for x in range(g[0], g[1] + 1)]}[kind]()
        for x, y in pts:
            if 0 <= x < w and 0 <= y < h:
                lab[y, x] = -1 if n is None else N[n]
    return lab


def stylise_hair(lab, reg, a, N, opts):
    """Hair = black mass + a couple of brown highlight clusters taken from the photo's lighter strands."""
    hair = reg == 0
    # only texture the inner mass: >=3px from the face and >=2px from the silhouette edge
    m = hair & (ndi.distance_transform_edt(hair | (reg < 0)) > 5) & (ndi.distance_transform_edt(reg >= 0) > 2)
    skin_rows = np.nonzero((reg == 1).any(1))[0]
    if len(skin_rows):  # crown only: rows above the forehead
        m[skin_rows[0] + 2:] = False
    if m.sum() < 20:
        return lab
    L = ndi.gaussian_filter(luma(a), opts.get("hair_sigma", 0.8))
    p_hi, p_mid = np.percentile(L[m], opts.get("hair_pct", (96, 88)))
    out = lab.copy()
    out[hair] = N["hair0"]
    hi = m & (L > p_mid)
    cl, n = ndi.label(hi, structure=np.ones((3, 3)))
    if n:
        hi &= np.isin(cl, 1 + np.nonzero(ndi.sum(hi, cl, range(1, n + 1)) >= 3)[0])
    out[hi] = N["hair1"]
    out[hi & ndi.binary_erosion(hi) & (L > p_hi)] = N["hair2"]  # brightest core only
    return out


def stylise_shirt(lab, reg, a, N, opts):
    """Shirt = flat shading clusters + regular 1px vertical pin-stripes (period 3)."""
    m = reg == 2
    if not m.any():
        return lab
    L = ndi.gaussian_filter(luma(a), 1.2)
    lo, hi = np.percentile(L[m], [25, 70])
    yy, xx = np.indices(lab.shape)
    period = opts.get("stripe_period", 3)
    out = lab.copy()
    base = np.where(L > hi, N["shirt4"], np.where(L > lo, N["shirt3"], N["shirt2"]))
    stripe = np.where(L > lo, N["shirt1"], N["shirt_ink"])
    # light dither between mid and shadow bands only
    band = (np.abs(L - lo) < 4) & ((xx + yy) % 2 == 0)
    base = np.where(band, N["shirt2"], base)
    is_stripe = (xx % period) == 0
    out[m] = np.where(is_stripe, stripe, base)[m]
    return out


def stylise_tie(lab, reg, N):
    m = reg == 3
    if not m.any():
        return lab
    m = ndi.binary_opening(m) | (m & ndi.binary_erosion(m))
    yy, xx = np.indices(lab.shape)
    out = lab.copy()
    inner = ndi.binary_erosion(m)
    dots = ((xx % 4 == 0) & (yy % 4 == 0)) | ((xx % 4 == 2) & (yy % 4 == 2))
    out[m] = N["tie0"]
    out[inner] = N["tie1"]
    out[inner & dots] = N["tie3"]
    out[inner & np.roll(dots, 1, 0) & np.roll(dots, 1, 1)] = N["tie2"]
    return out


def outline(lab, alpha, reg, N):
    """1px darker outline on the silhouette (4-neighbour edge), except the frame's bottom."""
    pad = np.pad(alpha, 1, constant_values=False)
    pad[-1, :] = pad[-2, :]  # body continues below the frame: no outline there
    edge = alpha & ~ndi.binary_erosion(pad, structure=[[0, 1, 0], [1, 1, 1], [0, 1, 0]])[1:-1, 1:-1]
    ink = {0: "ink", 1: "skin_ink", 2: "shirt_ink", 3: "ink"}
    out = lab.copy()
    out[~alpha] = -1
    for ri, n in ink.items():
        out[edge & (reg == ri)] = N[n]
    out[edge & (reg < 0)] = N["ink"]
    return out


def grid_view(lab, pal, x0, y0, x1, y1, z=12, photo=None):
    """Zoomed crop with a coordinate grid, for hand-tuning pixels."""
    sub = to_rgba(lab, pal)[y0:y1, x0:x1]
    bg = Image.new("RGBA", ((x1 - x0) * z, (y1 - y0) * z), (190, 190, 190, 255))
    bg.alpha_composite(Image.fromarray(sub).resize(bg.size, Image.NEAREST))
    d = ImageDraw.Draw(bg)
    for i in range(x1 - x0 + 1):
        d.line([(i * z, 0), (i * z, bg.size[1])], fill=(0, 255, 0, 255) if (x0 + i) % 10 == 0 else (90, 90, 90, 255))
        if (x0 + i) % 5 == 0:
            d.text((i * z + 1, 1), str(x0 + i), fill=(255, 0, 255, 255))
    for j in range(y1 - y0 + 1):
        d.line([(0, j * z), (bg.size[0], j * z)], fill=(0, 255, 0, 255) if (y0 + j) % 10 == 0 else (90, 90, 90, 255))
        if (y0 + j) % 5 == 0:
            d.text((1, j * z + 1), str(y0 + j), fill=(255, 0, 255, 255))
    return bg


def to_rgba(lab, pal):
    h, w = lab.shape
    o = np.zeros((h, w, 4), np.uint8)
    m = lab >= 0
    o[m, :3] = pal[lab[m]]
    o[m, 3] = 255
    return o


# ---------------------------------------------------------------- build + export
def build():
    """Palette from the 128 master, then every native size rendered against that one palette."""
    from master_tune import STROKES_128, scale_geom
    from size_tune import TUNE
    a, alpha = prep(HS, 128)
    pal, names = build_palette(a, regions(a, alpha, HS), raw_small(HS, 128))
    labs = {128: render(HS, 128, pal, names, {"strokes": STROKES_128, "majority": 2, "skin_sigma": 1.1})}
    for sz in (96, 64):  # mid sizes: native renders of the same head-and-shoulders crop
        labs[sz] = render(HS, sz, pal, names, {"strokes": scale_geom(STROKES_128, sz / 128) + TUNE[sz], "majority": 2,
                                               "skin_sigma": 1.1 * sz / 128, "prep": {"detail": 0.4}})
    for sz in (48, 32, 16):  # small sizes: native renders of the tight face crop
        labs[sz] = render(FACE, sz, pal, names, {"strokes": TUNE[sz], "stripes": sz >= 32, "majority": 1, "skin_sigma": 0.6,
                                                 "dither": sz >= 32, "prep": {"detail": 0.8}, "feat_pct": 10})
    return pal, names, labs


def up(im, k):
    return im.resize((im.width * k, im.height * k), Image.NEAREST)


def tile(size, colour, pattern=None):
    """Solid tile, or a 2-tone checker in `pattern` = (colour2, cell px)."""
    t = Image.new("RGBA", size, tuple(colour) + (255,))
    if pattern:
        c2, cell = pattern
        yy, xx = np.indices((size[1], size[0]))
        chk = ((xx // cell + yy // cell) % 2 == 0)
        arr = np.array(t)
        arr[chk, :3] = c2
        t = Image.fromarray(arr)
    return t


def export(pal, names, labs, out):
    out.mkdir(parents=True, exist_ok=True)
    N = {n: i for i, n in enumerate(names)}
    P = lambda n: tuple(int(v) for v in pal[N[n]])
    spr = {s: Image.fromarray(to_rgba(l, pal)) for s, l in labs.items()}
    files = {}

    def save(name, im, **kw):
        im.save(out / name, **kw)
        files[name] = im

    for s in (16, 32, 48, 64, 96, 128):
        save(f"oscar-{s}.png", spr[s])
    for k in (2, 4, 8):
        save(f"oscar-{128 * k}.png", up(spr[128], k))
    # favicon: the three native small sprites, not resamples of one image
    spr[48].save(out / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)],
                 append_images=[spr[16], spr[32]])
    # social avatar: 3x master (384) centred on a 400 solid tile
    av = tile((400, 400), P("sky"))
    av.alpha_composite(up(spr[128], 3), (8, 8))
    save("oscar-avatar-400.png", av)
    # Open Graph: 4x sprite bottom-left, 2-tone checker (8px = 2 sprite px) in palette blues, right side free for a name
    og = tile((1200, 630), P("deep"), (P("tie1"), 8))
    og.alpha_composite(up(spr[128], 4), (56, 630 - 512))
    save("oscar-og-1200x630.png", og)
    # edge-contrast checks for the 512
    for tag, bg in (("dark", "ink"), ("light", "shirt4")):
        t = tile((512, 512), P(bg))
        t.alpha_composite(up(spr[128], 4))
        save(f"oscar-512-on-{tag}.png", t)
    return files, spr


def verify(pal, labs, files, out):
    """Palette-only colours, binary alpha, exact sizes, integer-NN upscales, favicon frames identical to natives."""
    allowed = {tuple(int(v) for v in c) for c in pal}
    expect = {f"oscar-{s}.png": (s, s) for s in (16, 32, 48, 64, 96, 128, 256, 512, 1024)}
    expect.update({"oscar-avatar-400.png": (400, 400), "oscar-og-1200x630.png": (1200, 630),
                   "oscar-512-on-dark.png": (512, 512), "oscar-512-on-light.png": (512, 512)})
    problems, rows = [], []
    master = np.array(Image.open(out / "oscar-128.png"))
    for name, size in expect.items():
        a = np.array(Image.open(out / name).convert("RGBA"))
        ok_size = (a.shape[1], a.shape[0]) == size
        alpha_vals = set(np.unique(a[..., 3]).tolist())
        opaque = a[a[..., 3] == 255][:, :3]
        cols = {tuple(c) for c in np.unique(opaque, axis=0).tolist()} if len(opaque) else set()
        off = cols - allowed
        ok = ok_size and alpha_vals <= {0, 255} and not off
        # no smoothing: upscales must equal the master blown up by pixel repetition
        k = size[0] // 128 if name in ("oscar-256.png", "oscar-512.png", "oscar-1024.png") else 0
        if k:
            ok &= np.array_equal(a, np.repeat(np.repeat(master, k, 0), k, 1))
        rows.append((name, size, len(cols), ok))
        if not ok:
            problems.append((name, ok_size, alpha_vals, off))
    ico = Image.open(out / "favicon.ico")
    ico_sizes = sorted(ico.info.get("sizes", []))
    for s in (16, 32, 48):
        ico.size = (s, s)
        fr = np.array(ico.convert("RGBA"))
        same = np.array_equal(fr, np.array(Image.open(out / f"oscar-{s}.png").convert("RGBA")))
        rows.append((f"favicon.ico[{s}]", (s, s), None, same))
        if not same:
            problems.append((f"favicon {s}", "frame differs from native png"))
    return rows, problems, ico_sizes


def sprite_stats(lab):
    """Orphans (no same-colour 8-neighbour) and outline continuity of the silhouette edge."""
    h, w = lab.shape
    p = np.pad(lab, 1, constant_values=-9)
    orphans = 0
    for y, x in zip(*np.nonzero(lab >= 0)):
        nb = p[y:y + 3, x:x + 3].ravel().tolist()
        nb.pop(4)
        orphans += lab[y, x] not in nb
    alpha = lab >= 0
    pad = np.pad(alpha, 1, constant_values=False)
    pad[-1, :] = pad[-2, :]
    edge = alpha & ~ndi.binary_erosion(pad, structure=[[0, 1, 0], [1, 1, 1], [0, 1, 0]])[1:-1, 1:-1]
    comps = ndi.label(edge, structure=np.ones((3, 3)))[1]
    return orphans, comps


def contact_sheet(files, out):
    """preview.png: every export on neutral grey, with label + size; big ones shown at an integer reduction."""
    try:
        font = ImageFont.load_default(size=14)
    except TypeError:
        font = ImageFont.load_default()
    order = ["oscar-16.png", "oscar-32.png", "oscar-48.png", "favicon.ico", "oscar-64.png", "oscar-96.png", "oscar-128.png",
             "oscar-256.png", "oscar-512.png", "oscar-1024.png", "oscar-512-on-dark.png", "oscar-512-on-light.png",
             "oscar-avatar-400.png", "oscar-og-1200x630.png"]
    shown = {}
    for n in order:
        if n == "favicon.ico":
            ico = Image.open(out / n)
            fr = []
            for s in (16, 32, 48):
                ico.size = (s, s); fr.append(ico.convert("RGBA").copy())
            strip = Image.new("RGBA", (16 + 32 + 48 + 16, 48), (0, 0, 0, 0))
            x = 0
            for f in fr:
                strip.alpha_composite(f, (x, 48 - f.height)); x += f.width + 8
            shown[n] = (strip, "16+32+48", 1)
            continue
        im = files[n]
        k = max(1, -(-max(im.size) // 512))  # integer reduction so nothing exceeds 512
        while im.width % k or im.height % k:
            k += 1
        disp = im.resize((im.width // k, im.height // k), Image.NEAREST) if k > 1 else im
        shown[n] = (disp, f"{im.width}x{im.height}", k)
    pad, lab_h, W = 24, 40, 1800
    # simple row packing
    rows, cur, cw = [], [], 0
    for n in order:
        w = max(shown[n][0].width, 150) + pad
        if cw + w > W - pad and cur:
            rows.append(cur); cur, cw = [], 0
        cur.append(n); cw += w
    rows.append(cur)
    H = pad + sum(max(shown[n][0].height for n in r) + lab_h + pad for r in rows) + 30
    sheet = Image.new("RGBA", (W, H), (128, 128, 128, 255))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 8), "Oscar pixel logo - all exports (large files shown at an integer nearest-neighbour reduction)",
           fill=(20, 20, 20, 255), font=font)
    y = pad + 16
    for r in rows:
        x, rh = pad, max(shown[n][0].height for n in r)
        for n in r:
            im, sz, k = shown[n]
            sheet.alpha_composite(im, (x, y + rh - im.height))
            note = f"{sz}" + (f"  (shown 1/{k})" if k > 1 else "")
            d.text((x, y + rh + 4), n, fill=(10, 10, 10, 255), font=font)
            d.text((x, y + rh + 20), note, fill=(40, 40, 40, 255), font=font)
            x += max(im.width, 150) + pad
        y += rh + lab_h + pad
    sheet.convert("RGB").save(out / "preview.png")


def main():
    out = OUT_DIR
    pal, names, labs = build()
    files, spr = export(pal, names, labs, out)
    rows, problems, ico_sizes = verify(pal, labs, files, out)
    contact_sheet(files, out)
    # palette swatch for reference (also palette-only)
    sw = Image.new("RGBA", (len(pal) * 16, 16))
    for i, c in enumerate(pal):
        sw.paste(tuple(int(v) for v in c) + (255,), (i * 16, 0, i * 16 + 16, 16))
    sw.save(out / "palette.png")
    with open(out / "palette.txt", "w") as f:
        for n, c in zip(names, pal):
            f.write(f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}  {n}\n")
    if "--debug" in sys.argv:
        import pickle
        pickle.dump((labs, pal, names), open(out / "debug_labs.pkl", "wb"))

    print(f"\nOutput: {out.resolve()}")
    print(f"{'file':28s} {'size':>10s} {'colours':>8s}  check")
    for n, sz, nc, ok in rows:
        print(f"{n:28s} {sz[0]:>4d}x{sz[1]:<5d} {('' if nc is None else nc):>8}  {'OK' if ok else 'FAIL'}")
    print(f"favicon.ico sizes: {ico_sizes}")
    for s in (128, 96, 64, 48, 32, 16):
        o, c = sprite_stats(labs[s])
        print(f"sprite {s:>3d}: {len(np.unique(labs[s][labs[s] >= 0]))} colours, orphan px {o}, outline pieces {c}")
    print(f"\nShared palette ({len(pal)} colours):")
    print("  " + " ".join(f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}" for c in pal))
    print("PROBLEMS:" if problems else "All checks passed.", *problems, sep="\n  ")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
