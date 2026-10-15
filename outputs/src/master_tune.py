"""Hand-tuned strokes for the 128 master (coordinates read off the photo-at-128 grid)."""
W = "shirt2"  # eye white: warm grey-white, not pure white

STROKES_128 = [
    # collar wings: light flat with diagonal pinstripes, dark lower edge
    ("polystripe", ([(48, 80), (51, 80), (56, 91), (54, 94), (41, 104), (43, 92)], "shirt4", "shirt2", 3, (1, 1)), None),
    ("polystripe", ([(81, 79), (84, 80), (82, 92), (79, 103), (69, 93), (70, 90)], "shirt4", "shirt2", 3, (1, -1)), None),
    ("line", (55, 94, 41, 104), "shirt_ink"),
    ("line", (69, 93, 79, 103), "shirt_ink"),
    ("line", (49, 81, 55, 91), "shirt1"),
    ("line", (81, 80, 70, 91), "shirt1"),
    # left brow: thick, almost flat, outer tail dips
    ("h", (52, 61, 32), "hair0"), ("h", (51, 61, 33), "hair0"), ("h", (50, 51, 34), "hair0"),
    ("h", (52, 61, 31), "skin4"), ("h", (53, 61, 34), "skin3"),
    # right brow: slopes down and outward
    ("h", (75, 80, 35), "hair0"), ("h", (75, 84, 36), "hair0"), ("h", (79, 86, 37), "hair0"), ("h", (84, 87, 38), "hair0"),
    ("h", (75, 79, 34), "skin4"), ("h", (75, 84, 38), "skin3"), ("h", (73, 84, 39), "skin3"),
    # left eye: crease, heavy upper lid, 2-row opening, lower lid
    ("h", (53, 58, 38), "skin2"),
    ("h", (52, 59, 39), "ink"),
    ("px", [(51, 40), (60, 40)], "skin_ink"),
    ("px", [(52, 40), (53, 40), (54, 40), (58, 40), (59, 40), (53, 41), (54, 41), (58, 41)], W),
    ("px", [(55, 40), (57, 40), (55, 41), (56, 41), (57, 41)], "ink"),
    ("px", [(56, 40)], "shirt4"),
    ("px", [(52, 41), (59, 41)], "skin1"),
    ("h", (53, 58, 42), "skin1"),
    ("h", (53, 58, 43), "skin3"),
    # right eye (lower: head tilt)
    ("h", (73, 81, 42), "ink"),
    ("px", [(72, 43), (82, 43)], "skin_ink"),
    ("px", [(73, 43), (74, 43), (75, 43), (80, 43), (74, 44), (75, 44), (80, 44)], W),
    ("px", [(76, 43), (78, 43), (79, 43), (76, 44), (77, 44), (78, 44), (79, 44)], "ink"),
    ("px", [(77, 43)], "shirt4"),
    ("px", [(73, 44), (81, 44)], "skin1"),
    ("h", (74, 80, 45), "skin1"),
    ("h", (74, 81, 46), "skin3"),
    # nose: nostrils + tip highlight
    ("px", [(61, 57), (62, 57), (68, 58), (69, 58), (70, 57)], "skin0"),
    ("px", [(65, 54), (65, 55), (66, 55)], "skin6"),
    # mouth: upper lip, closed line with the right corner dropping, lower lip
    ("h", (57, 70, 67), "lips"),
    ("h", (55, 71, 68), "skin0"), ("px", [(72, 69), (54, 69)], "skin0"),
    ("h", (58, 69, 69), "lips"),
]


def scale_geom(strokes, f):
    """Scale only the geometric strokes (collar shapes/lines); facial pixel strokes are tuned per size."""
    out = []
    for kind, g, n in strokes:
        if kind == "polystripe":
            pts, *rest = g
            out.append((kind, ([(round(x * f), round(y * f)) for x, y in pts], *rest), n))
        elif kind == "line" and n == "shirt_ink":
            out.append((kind, tuple(round(v * f) for v in g), n))
    return out
