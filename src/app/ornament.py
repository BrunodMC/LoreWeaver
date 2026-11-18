"""Hand-made marks, drawn as SVG on the server: ink over teased secrets, cracks in the archive's marble,
an ink-wash portrait for the codex.

Everything is seeded (by entry, field and passage), so a mark is unique but stable: the same passage gets
the same ink on every page load and every publish. Nothing secret is ever drawn: a tease covers decoy
writing as long as the secret, never the secret's own words.
"""
import hashlib
import math
import random

from markupsafe import Markup, escape

SYLLABLES = ("an", "or", "el", "wyn", "mar", "dros", "ven", "ka", "is", "ne", "ul", "tham", "ser", "rin", "oth",
             "ae", "bel", "cor", "fen", "hal", "ist", "lor", "mun", "quel", "sa", "thi", "vor", "ith", "dun")
TEASE_STYLES = ("scored", "splash")
CHUNK = 16          # characters of decoy per inked run, so long secrets wrap like text
CHAR_W, LINE_H = 8.2, 27.0  # a run's drawing box in user units; the SVG stretches to the real text


def rng(*seed) -> random.Random:
    """A generator seeded by the given parts, the same in every process (unlike hash())."""
    digest = hashlib.sha256(":".join(map(str, seed)).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _f(x: float) -> str:
    return f"{x:.1f}"


# ── Decoy writing ──────────────────────────────────────────────────────────

def decoy_words(length: int, r: random.Random) -> list[str]:
    words, total = [], 0
    while total < length:
        word = "".join(r.choice(SYLLABLES) for _ in range(1 + r.randrange(3)))
        words.append(word)
        total += len(word) + 1
    return words


def chunk(words: list[str], size: int = CHUNK) -> list[str]:
    runs, current = [], []
    for word in words:
        if current and len(" ".join(current + [word])) > size:
            runs.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        runs.append(" ".join(current))
    return runs


# ── Pen strokes ────────────────────────────────────────────────────────────

def densify(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """A Catmull-Rom curve through the points, sampled finely."""
    out, n = [], len(points)
    for i in range(n - 1):
        p0, p1, p2, p3 = points[max(i - 1, 0)], points[i], points[i + 1], points[min(i + 2, n - 1)]
        steps = max(4, math.ceil(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) / 1.5))
        for k in range(steps):
            t = k / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3) for j in (0, 1)))
    out.append(points[-1])
    return out


def pen_stroke(points, width: float, r: random.Random) -> str:
    """A pen line as a filled outline whose width follows the pressure: it lands thin (or blunt, pressed
    hard), swells and wavers, and lifts off in a point. No round caps."""
    pts = densify(points)
    n = len(pts)
    landing = 0.012 if r.random() < 0.35 else 0.05 + r.random() * 0.12
    lift = 0.04 + r.random() * 0.16
    ph1, ph2, f1, f2 = r.random() * 6.3, r.random() * 6.3, 3 + r.random() * 5, 9 + r.random() * 9
    left, right = [], []
    for i, (x, y) in enumerate(pts):
        t = i / (n - 1)
        press = min(1, t / landing) ** 0.55 * min(1, (1 - t) / lift) ** 0.9
        waver = 0.82 + 0.12 * math.sin(f1 * t * 6.28 + ph1) + 0.06 * math.sin(f2 * t * 6.28 + ph2)
        w = max(0.15, width * press * waver) / 2
        a, b = pts[max(i - 1, 0)], pts[min(i + 1, n - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1
        nx, ny = -dy / length, dx / length
        left.append(f"{_f(x + nx * w)} {_f(y + ny * w)}")
        right.append(f"{_f(x - nx * w)} {_f(y - ny * w)}")
    return "M" + " L".join(left) + " L" + " L".join(reversed(right)) + " Z"


def scored(w: float, h: float, r: random.Random) -> str:
    """A hand's scribble: slanted zigzags (/|/|/|), forth and back, then a heavy line straight through."""
    mid, band = h * 0.53, h * 0.2
    shapes = []
    for p in range(3 + r.randrange(3)):
        x = -3 - r.random() * 5
        off, amp = (r.random() * 2 - 1) * band * 0.3, band * (0.85 + r.random() * 0.45)
        stride, up = h * (0.32 + r.random() * 0.2), True
        pts = [(x, mid + off + amp * (0.6 + r.random() * 0.4))]
        while x < w + 4:
            reach = amp * (0.75 + r.random() * 0.35) * (1.25 if r.random() < 0.1 else 1)
            x += stride * (0.85 + r.random() * 0.5) if up else stride * (-0.15 + r.random() * 0.35)
            pts.append((x, mid + off + (-reach if up else reach)))
            up = not up
        if p % 2:
            pts.reverse()
        shapes.append(pen_stroke(pts, 2.4 + r.random() * 2.4, r))
    for _ in range(1 + r.randrange(2)):
        y1, y2 = mid + (r.random() * 2 - 1) * band * 0.6, mid + (r.random() * 2 - 1) * band * 0.6
        sag = (r.random() * 2 - 1) * band * 0.5
        x0, x1 = -5 - r.random() * 4, w + 3 + r.random() * 6
        shapes.append(pen_stroke([(x0, y1), (x0 + (x1 - x0) * 0.35, (y1 + y2) / 2 + sag),
                                  (x0 + (x1 - x0) * 0.7, (y1 + y2) / 2 - sag * 0.4), (x1, y2)], 4.2 + r.random() * 2.8, r))
    return '<g filter="url(#ink-stroke)">' + "".join(f'<path d="{d}"/>' for d in shapes) + "</g>"


# ── Splashes ───────────────────────────────────────────────────────────────

def _splash(cx, cy, rx, ry, limit, r):
    """A body with narrow arms shot outward; between neighbouring arms the edge bulges out as a lobe."""
    arms = []
    for _ in range(5 + r.randrange(5)):
        th = r.random() * math.tau
        arms.append((th, 0.05 + r.random() * 0.09, (0.35 + r.random() * 0.9) * ry * (0.3 + 1.1 * abs(math.cos(th)))))
    body = [(r.random() * 0.08 / (k + 1), r.random() * 6.3, k + 2) for k in range(3)]
    ordered = sorted(arms)
    gaps = []
    for i, (th, _, _) in enumerate(ordered):
        nxt = ordered[i + 1][0] if i + 1 < len(ordered) else ordered[0][0] + math.tau
        gaps.append((th, nxt - th, (0.16 + r.random() * 0.14) * min(1, (nxt - th) / 1.2)))

    def base(t):
        c, s = math.cos(t), math.sin(t)
        return rx * ry / math.sqrt((ry * c) ** 2 + (rx * s) ** 2)

    pts, n = [], 160
    for k in range(n):
        t = k / n * math.tau
        rad = base(t) * (1 + sum(a * math.sin(m * t + ph) for a, ph, m in body))
        for start, span, bulge in gaps:
            u = (t - start) % math.tau
            if u < span:
                rad += base(t) * bulge * math.sin(math.pi * u / span)
        for th, width, length in arms:
            d = math.atan2(math.sin(t - th), math.cos(t - th))
            rad += length * math.exp(-((d / width) ** 2))
        s = math.sin(t)
        if abs(s) > 0.01:
            rad = min(rad, limit / abs(s))  # never past the line's height
        pts.append((cx + math.cos(t) * rad, cy + s * rad))
    d = f"M{_f((pts[0][0] + pts[-1][0]) / 2)} {_f((pts[0][1] + pts[-1][1]) / 2)}"
    for i, (x, y) in enumerate(pts):
        qx, qy = pts[(i + 1) % n]
        d += f" Q{_f(x)} {_f(y)} {_f((x + qx) / 2)} {_f((y + qy) / 2)}"
    tips = [(th, base(th) + length) for th, _, length in arms if length >= ry * 0.5]
    return d + " Z", tips


def splash(w: float, h: float, r: random.Random) -> str:
    cy0, limit = h * 0.52, h * 0.47
    count = max(1, round(w / (h * 1.05)))
    parts = []
    for i in range(count):
        cx = w * (i + 0.5) / count + (r.random() * 2 - 1) * w / count * 0.1
        cy = cy0 + (r.random() * 2 - 1) * h * 0.03
        rx, ry = w / count * (0.5 + r.random() * 0.12), h * (0.26 + r.random() * 0.06)
        d, tips = _splash(cx, cy, rx, ry, limit - abs(cy - cy0), r)
        parts.append(f'<path d="{d}"/>')
        for th, reach in tips:  # a droplet past each long arm, stretched along its flight
            if r.random() < 0.25:
                continue
            dist = reach + 1.5 + r.random() * 4
            x, y = cx + math.cos(th) * dist, cy + math.sin(th) * dist
            if abs(y - cy0) > limit:
                continue
            size = 0.6 + r.random() * 1.5
            parts.append(f'<ellipse cx="{_f(x)}" cy="{_f(y)}" rx="{size * (1.3 + r.random() * 1.2):.2f}" ry="{size:.2f}" '
                         f'transform="rotate({math.degrees(th):.0f} {_f(x)} {_f(y)})"/>')
        for _ in range(2 + r.randrange(4)):  # tiny satellites flung furthest, mostly sideways
            ang = (0 if r.random() < 0.5 else math.pi) + (r.random() * 2 - 1) * 0.45
            dist = rx * (1.25 + r.random() * 0.5)
            fx, fy = cx + math.cos(ang) * dist, cy + math.sin(ang) * dist * 0.5
            if abs(fy - cy0) <= limit:
                parts.append(f'<circle cx="{_f(fx)}" cy="{_f(fy)}" r="{0.35 + r.random() * 0.8:.2f}"/>')
    return '<g filter="url(#ink-blot)">' + "".join(parts) + "</g>"


# ── Teases ─────────────────────────────────────────────────────────────────

def tease(length: int, *seed) -> Markup:
    """Decoy writing as long as the secret, struck out or splashed over in ink. The style is picked from
    the seed, so a passage keeps its style; different passages vary."""
    r = rng("tease", *seed)
    style = r.choice(TEASE_STYLES)
    draw = scored if style == "scored" else splash
    runs = []
    for text in chunk(decoy_words(max(3, length), r)):
        w, h = len(text) * CHAR_W + 10, LINE_H
        runs.append(Markup('<span class="tz"><span class="decoy">{}</span>'
                           '<svg viewBox="0 0 {} {}" preserveAspectRatio="none" aria-hidden="true">{}</svg></span>')
                    .format(text, _f(w), _f(h), Markup(draw(w, h, r))))
    label = "A line here has been struck out" if style == "scored" else "A line here is lost under spilled ink"
    return Markup('<span class="tease tease-{}" role="img" aria-label="{}">{}</span>').format(style, label, Markup("").join(runs))


INK_DEFS = Markup("""<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false"><defs>
<filter id="ink-stroke" x="-5%" y="-40%" width="110%" height="180%">
<feTurbulence type="fractalNoise" baseFrequency="0.05 0.09" numOctaves="2" seed="3" result="n"/>
<feDisplacementMap in="SourceGraphic" in2="n" scale="1.6" xChannelSelector="R" yChannelSelector="G" result="d"/>
<feTurbulence type="fractalNoise" baseFrequency="0.015 0.9" numOctaves="2" seed="11" result="g"/>
<feColorMatrix in="g" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -0.45 1.28" result="ga"/>
<feComposite in="d" in2="ga" operator="in"/></filter>
<filter id="ink-blot" x="-20%" y="-60%" width="140%" height="220%">
<feTurbulence type="fractalNoise" baseFrequency="0.07" numOctaves="2" seed="5" result="n"/>
<feDisplacementMap in="SourceGraphic" in2="n" scale="1.8" xChannelSelector="R" yChannelSelector="G"/></filter>
</defs></svg>""")


# ── Cracks in the archive's marble ─────────────────────────────────────────

CRACK_W, CRACK_H = 1000, 700


def cracks(*seed) -> Markup:
    """Fractures for one entry's slab: a steady course, jagged at a small scale with the odd kink that
    snaps back, widest at the origin and tapering to nothing, with acute short branches. Each is drawn
    twice: a faint light lip offset beside the dark fissure, so it reads as cut into the stone."""
    r = rng("cracks", *seed)
    lips, cuts = [], []

    def taper(pts, w0):
        n, left, right = len(pts), [], []
        for i, (x, y) in enumerate(pts):
            t = i / (n - 1)
            w = w0 * (1 - t) ** 0.75 * (0.75 + r.random() * 0.5) / 2 + 0.05
            a, b = pts[max(i - 1, 0)], pts[min(i + 1, n - 1)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy) or 1
            left.append(f"{_f(x - dy / length * w)} {_f(y + dx / length * w)}")
            right.append(f"{_f(x + dy / length * w)} {_f(y - dx / length * w)}")
        return "M" + " L".join(left) + " L" + " L".join(reversed(right)) + " Z"

    def crack(x, y, course, length, width, depth):
        pts, heading, walked = [(x, y)], course, 0.0
        while walked < length:
            heading += (course - heading) * 0.35 + (r.random() * 2 - 1) * 0.28
            if r.random() < 0.07:
                heading += (1 if r.random() < 0.5 else -1) * (0.5 + r.random() * 0.4)
            step = 2 + r.random() * 4
            x, y = x + math.cos(heading) * step, y + math.sin(heading) * step
            walked += step
            pts.append((x, y))
            if depth < 2 and walked > length * 0.15 and r.random() < 0.035:
                side = 1 if r.random() < 0.5 else -1
                crack(x, y, heading + side * (0.35 + r.random() * 0.35), (length - walked) * (0.25 + r.random() * 0.3),
                      width * (1 - walked / length) * 0.75, depth + 1)
        if len(pts) < 3:
            return  # a branch born at its parent's very end: nothing to draw
        d = taper(pts, width)
        lips.append(f'<path d="{d}" transform="translate(0.8 0.9)"/>')
        cuts.append(f'<path d="{d}"/>')

    # Two to four cracks, each entering from a different edge and running inward.
    edges = [("right", lambda: (CRACK_W + 2, CRACK_H * (0.1 + r.random() * 0.35)), math.pi * (0.78 + r.random() * 0.12)),
             ("bottom", lambda: (CRACK_W * (0.35 + r.random() * 0.4), CRACK_H + 2), -math.pi * (0.5 + r.random() * 0.2)),
             ("left", lambda: (-2, CRACK_H * (0.45 + r.random() * 0.4)), -0.1 - r.random() * 0.25),
             ("top", lambda: (CRACK_W * (0.55 + r.random() * 0.4), -2), math.pi * (0.55 + r.random() * 0.15))]
    r.shuffle(edges)
    for i, (_, start, course) in enumerate(edges[:2 + r.randrange(3)]):
        x, y = start()
        crack(x, y, course, (CRACK_W * 0.5 if i == 0 else CRACK_W * 0.28) * (0.75 + r.random() * 0.5),
              1.9 if i == 0 else 1.3, 0)
    return Markup(f'<g class="crack-lip">{"".join(lips)}</g><g class="crack-cut">{"".join(cuts)}</g>')


# ── The codex leaf: lichen in the corners, foxing on the paper ─────────────

LICHEN_SIZE = 260
CORNERS = ("tl", "tr", "bl", "br")


def _lobe(cx, cy, radius, r):
    """One lichen lobe: a small round blob with a crinkled edge."""
    n = 14
    pts = []
    for k in range(n):
        t = k / n * math.tau
        rad = radius * (0.78 + r.random() * 0.4)
        pts.append((cx + math.cos(t) * rad, cy + math.sin(t) * rad))
    d = f"M{_f((pts[0][0] + pts[-1][0]) / 2)} {_f((pts[0][1] + pts[-1][1]) / 2)}"
    for i, (x, y) in enumerate(pts):
        qx, qy = pts[(i + 1) % n]
        d += f" Q{_f(x)} {_f(y)} {_f((x + qx) / 2)} {_f((y + qy) / 2)}"
    return d + " Z"


def lichen(*seed) -> Markup:
    """Lichen creeping in from one or two corners of a leaf. Drawn for the top-left corner; CSS mirrors
    it into whichever corner the seed picked. Rosettes of overlapping lobes (densest at the corner, paler
    at the rims), a few ochre cups, and tufts of moss right in the corner."""
    r = rng("lichen", *seed)
    corners = r.sample(CORNERS, 1 + (r.random() < 0.55))
    out = []
    for corner in corners:
        s = LICHEN_SIZE
        rosettes, cups, moss = [], [], []
        for _ in range(5 + r.randrange(4)):  # rosettes, each a cluster of lobes around its own centre
            if r.random() < 0.45:  # creeping along one edge of the page, hugging it
                along, off = r.random() ** 1.3 * s * 0.85, r.random() ** 2.5 * s * 0.08
                ox, oy = (off, along) if r.random() < 0.5 else (along, off)
            else:  # bunched into the corner itself
                ox, oy = (r.random() ** 2.4) * s * 0.3, (r.random() ** 2.4) * s * 0.3
            spread = 10 + r.random() * 22
            for _ in range(10 + r.randrange(16)):
                ang, dist = r.random() * math.tau, (r.random() ** 0.7) * spread
                x, y = ox + math.cos(ang) * dist, oy + math.sin(ang) * dist
                if x < -8 or y < -8:
                    continue
                size = (3 + r.random() * 9) * (1.25 - min(1, math.hypot(x, y) / s))
                tone = "lichen-a" if r.random() < 0.6 else "lichen-b"
                rosettes.append(f'<path class="{tone}" d="{_lobe(x, y, max(1.5, size), r)}"/>')
                if r.random() < 0.06:
                    cups.append(f'<circle class="lichen-cup" cx="{_f(x)}" cy="{_f(y)}" r="{1.2 + r.random() * 2.2:.2f}"/>')
        for _ in range(14 + r.randrange(14)):  # moss: short curling strokes tucked into the corner
            x, y = r.random() ** 2 * s * 0.22, r.random() ** 2 * s * 0.22
            ang, length = r.random() * math.tau, 3 + r.random() * 7
            moss.append(f'<path class="moss-tuft" d="M{_f(x)} {_f(y)} q {_f(math.cos(ang) * length * 0.6)} '
                        f'{_f(math.sin(ang) * length * 0.6 - 2)} {_f(math.cos(ang) * length)} {_f(math.sin(ang) * length)}"/>')
        out.append(f'<svg class="lichen lichen-{corner}" viewBox="0 0 {s} {s}" width="{s}" height="{s}" aria-hidden="true" '
                   f'focusable="false">{"".join(rosettes)}{"".join(cups)}{"".join(moss)}</svg>')
    return Markup("".join(out))


def foxing(*seed) -> Markup:
    """Age spots on the paper: a few faint brown stains, soft-edged, scattered by the seed."""
    r = rng("foxing", *seed)
    spots = []
    for _ in range(2 + r.randrange(4)):
        x, y, rad = r.random() * 1000, r.random() * 1000, 4 + r.random() * 16
        spots.append(f'<ellipse cx="{_f(x)}" cy="{_f(y)}" rx="{_f(rad)}" ry="{_f(rad * (0.7 + r.random() * 0.5))}" '
                     f'fill="url(#fox)" opacity="{0.2 + r.random() * 0.35:.2f}"/>')
    return Markup('<svg class="foxing" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
                  '<defs><radialGradient id="fox"><stop offset="0" class="fox-core"/><stop offset="0.7" class="fox-mid"/>'
                  '<stop offset="1" class="fox-edge"/></radialGradient>'
                  '<filter id="grain" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" '
                  'baseFrequency="0.9" numOctaves="3" seed="7" result="n"/><feColorMatrix in="n" type="matrix" '
                  'values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.09 -0.02"/></filter></defs>'
                  '<rect class="grain" width="1000" height="1000" filter="url(#grain)"/>'
                  + "".join(spots) + "</svg>")


# ── An ink-wash portrait, until there's a real one ─────────────────────────

def portrait(*seed) -> Markup:
    """A figure suggested in overlapping translucent washes: reeds or ground, shoulders, a head."""
    r = rng("portrait", *seed)
    w, h = 420, 525
    shapes = []

    def wash(cls, alpha, cx, cy, rx, ry, n):
        for _ in range(n):
            a = alpha * (0.5 + r.random() * 0.5)
            shapes.append(f'<ellipse class="{cls}" cx="{_f(cx + (r.random() - 0.5) * rx * 0.3)}" cy="{_f(cy + (r.random() - 0.5) * ry * 0.3)}" '
                          f'rx="{_f(rx * (0.7 + r.random() * 0.4))}" ry="{_f(ry * (0.7 + r.random() * 0.4))}" '
                          f'transform="rotate({(r.random() - 0.5) * 23:.0f} {_f(cx)} {_f(cy)})" opacity="{a:.3f}"/>')

    lean = (r.random() - 0.5) * w * 0.12
    wash("moss", 0.10, w * 0.5, h * 0.95, w * 0.7, h * 0.25, 10)
    wash("ink", 0.10, w * 0.5 + lean * 0.4, h * 0.78, w * (0.32 + r.random() * 0.08), h * 0.24, 12)
    wash("ink", 0.12, w * 0.5 + lean, h * (0.4 + r.random() * 0.05), w * (0.18 + r.random() * 0.04), h * 0.17, 12)
    wash("moss", 0.08, w * 0.5 + lean, h * 0.36, w * 0.17, h * 0.1, 6)
    for _ in range(18):
        x = r.random() * w
        shapes.append(f'<path class="stroke" d="M{_f(x)} {h} Q{_f(x + (r.random() - 0.5) * 30)} {_f(h * 0.8)} '
                      f'{_f(x + (r.random() - 0.5) * 50)} {_f(h * (0.6 + r.random() * 0.15))}"/>')
    return Markup(f'<svg class="wash" viewBox="0 0 {w} {h}" aria-hidden="true" focusable="false">'
                  f'<rect class="paper" width="{w}" height="{h}"/>{"".join(shapes)}</svg>')
