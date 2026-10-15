"""Hand-tuned facial features for the native non-master sizes.
Coordinates were mapped from photo-pixel feature positions into each size's grid, then nudged by eye."""
W = "shirt2"  # eye white


def eye(x0, lid_y, w, pupil, rows=2, glint=None, lower="skin2", corner="skin_ink"):
    """Lid line, `rows` open rows with whites either side of a dark pupil, lower lid."""
    s = [("h", (x0, x0 + w - 1, lid_y), "ink")]
    for r in range(rows):
        y = lid_y + 1 + r
        for x in range(x0, x0 + w):
            s.append(("px", [(x, y)], "ink" if x in pupil else W))
    if corner:
        s.append(("px", [(x0 - 1, lid_y + 1)], corner))
    s.append(("h", (x0 + 1, x0 + w - 2, lid_y + rows + 1), lower))
    if glint:
        s.append(("px", [glint], "shirt4"))
    return s


LEGEND = {"K": "ink", "H": "hair0", "o": "skin_ink", "L": "lips", "W": "shirt4", "w": "shirt3", "s": "shirt1",
          "i": "shirt_ink", **{str(i): f"skin{i}" for i in range(7)}}

TUNE = {
    96: [
        ("clear", (37, 22, 46, 31), None), ("clear", (54, 25, 64, 35), None), ("clear", (39, 49, 55, 53), None),
        ("h", (39, 45, 24), "hair0"), ("h", (38, 45, 25), "hair0"), ("px", [(37, 26)], "hair0"),
        ("h", (56, 60, 26), "hair0"), ("h", (57, 63, 27), "hair0"), ("h", (61, 65, 28), "hair0"),
        *eye(39, 29, 6, (41, 42), glint=(42, 30)),
        *eye(55, 32, 6, (57, 58, 59), glint=(58, 33), lower="skin1"),
        ("px", [(46, 43), (51, 43), (52, 43)], "skin0"), ("px", [(49, 41)], "skin6"),
        ("h", (43, 52, 50), "lips"), ("h", (41, 53, 51), "skin0"), ("px", [(40, 52), (54, 52)], "skin0"),
        ("h", (44, 51, 52), "lips"),
    ],
    64: [
        ("clear", (24, 14, 31, 23), None), ("clear", (35, 15, 44, 24), None), ("clear", (27, 32, 37, 36), None),
        ("h", (26, 30, 16), "hair0"), ("h", (25, 30, 17), "hair0"),
        ("h", (37, 40, 17), "hair0"), ("h", (39, 42, 18), "hair0"), ("px", [(43, 19)], "hair0"),
        *eye(26, 19, 4, (27, 28), rows=1, corner=None),
        *eye(37, 21, 4, (38, 39), rows=1, corner=None, lower="skin1"),
        ("px", [(31, 28), (34, 29)], "skin0"), ("px", [(32, 27)], "skin6"),
        ("h", (29, 34, 33), "lips"), ("h", (28, 35, 34), "skin0"), ("px", [(36, 35)], "skin0"),
        ("h", (30, 33, 35), "lips"),
    ],
    48: [
        ("clear", (13, 11, 22, 22), None), ("clear", (28, 13, 39, 24), None), ("clear", (16, 35, 30, 39), None),
        ("h", (15, 21, 13), "hair0"), ("h", (14, 21, 14), "hair0"), ("px", [(13, 15)], "hair0"),
        ("h", (30, 33, 15), "hair0"), ("h", (30, 36, 16), "hair0"), ("h", (34, 38, 17), "hair0"),
        *eye(15, 18, 6, (17, 18), glint=(18, 19)),
        *eye(29, 20, 6, (31, 32), glint=(32, 21), lower="skin1"),
        ("px", [(21, 30), (26, 30)], "skin0"), ("px", [(23, 28)], "skin6"),
        ("h", (19, 27, 36), "lips"), ("h", (17, 28, 37), "skin0"), ("px", [(29, 38)], "skin0"),
        ("h", (19, 26, 38), "lips"),
    ],
    32: [
        ("clear", (8, 8, 15, 15), None), ("clear", (18, 9, 26, 16), None), ("clear", (10, 23, 20, 27), None),
        ("h", (10, 14, 9), "hair0"), ("px", [(9, 10)], "hair0"),
        ("h", (20, 22, 10), "hair0"), ("h", (22, 25, 11), "hair0"),
        *eye(10, 12, 4, (11, 12), rows=1, corner=None),
        *eye(19, 13, 4, (20, 21), rows=1, corner=None, lower="skin1"),
        ("px", [(14, 20), (17, 20)], "skin0"),
        ("h", (13, 17, 24), "lips"), ("h", (12, 18, 25), "skin0"), ("px", [(19, 26)], "skin0"),
    ],
    # 16px: painted by hand on the auto silhouette (rows 14-15 = auto collar)
    16: [("map", (LEGEND, [
        ". . . K K K K K K K K K K . . .",
        ". . K H H H H H H H H H H K . .",
        ". . K H H 5 6 6 6 6 5 H H K . .",
        ". . K H 4 6 6 6 6 6 6 5 H K . .",
        ". . K H 4 H H H 6 6 5 5 3 K . .",
        ". . K 3 5 6 6 6 6 6 H H 3 K . .",
        ". o 2 3 5 1 K 6 6 6 5 5 H K . .",
        ". o 2 3 5 6 6 6 5 6 K 1 3 K . .",
        ". o 2 3 5 6 6 6 5 5 5 4 2 K . .",
        ". . o 3 5 6 6 6 6 4 5 4 o . . .",
        ". . . o 5 6 6 2 3 2 5 4 o . . .",
        ". . . o 4 5 5 5 5 5 4 2 o . . .",
        ". . . o 3 4 0 0 0 0 2 o . . . .",
        ". . o 2 2 4 3 L L 3 2 o . . . .",
        "? ? ? ? ? ? ? ? ? ? ? ? ? ? ? ?",
        "? ? ? ? ? ? ? ? ? ? ? ? ? ? ? ?",
    ]), None)],
}
