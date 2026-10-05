"""Lightweight, deterministic artwork shared by the HUD and Settings preview."""
import math
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ImageChops

KEY = (0, 0, 1)
ANGLES = tuple(math.tau * i / 96 for i in range(97))


@lru_cache(maxsize=12)
def _fonts(width, scale):
    # Physical minimums keep Compact useful instead of shrinking every glyph.
    sizes = (max(11, 11 * width / 152), max(8, 8 * width / 152))
    try:
        return (ImageFont.truetype("segoeuib.ttf", round(sizes[0] * scale)),
                ImageFont.truetype("segoeui.ttf", round(sizes[1] * scale)))
    except OSError:
        return ImageFont.load_default(), ImageFont.load_default()


def render_orb(width, height, state="listening", level=0.0, phase=0.0,
               hover=None, language="AUTO", scale=3):
    s = width / 152 * scale
    cx, cy = width * scale / 2, height * scale / 2
    img = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = (16, 29, 48, 205)
    colors = {
        "idle": ((100, 176, 255), (126, 151, 255)),
        "listening": ((83, 237, 214), (111, 156, 255)),
        "transcribing": ((188, 163, 255), (117, 183, 255)),
        "done": ((120, 243, 185), (71, 207, 223)),
    }
    primary, secondary = colors.get(state, colors["idle"])
    level = max(0.0, min(1.0, level)) if state == "listening" else 0.0

    def mix(a, b, amount):
        return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))

    def stroke(points, color, thickness=1.0):
        d.line(points, fill=ink, width=max(1, round((thickness + 1.5) * s)))
        d.line(points, fill=color, width=max(1, round(thickness * s)))

    # Three smooth currents suggest volume while leaving the center empty.
    # Fixed bounds prevent clipping at maximum voice level; no blur or GPU.
    for layer, radius in enumerate((51, 47, 42)):
        pts = []
        for a in ANGLES:
            ripple = math.sin(3 * a + phase * .65 + layer * 1.7)
            ripple += .35 * math.sin(5 * a - phase * .43 + layer)
            r = radius + 2.0 * level + (1.4 + level * 1.4) * ripple
            pts.append((cx + math.cos(a) * r * s,
                        cy + math.sin(a) * r * s))
        d.line(pts, fill=primary + (24,), width=max(1, round(6 * s)))
        d.line(pts, fill=ink, width=max(1, round(2.8 * s)))
        for i in range(len(pts) - 1):
            t = .5 + .5 * math.sin(ANGLES[i] + phase * .22 + layer * .9)
            color = mix(primary, secondary, t)
            if layer == 2:
                color = mix(color, (225, 249, 255), .2)
            d.line(pts[i:i+2], fill=color, width=max(1, round(1.25 * s)))

    # A broken outer orbit provides a quiet structure around the fluid center.
    orbit = 59 * s
    for offset, sweep in ((14, 32), (112, 11), (187, 38), (281, 14)):
        start = offset + (phase * 24 if state == "transcribing" else phase * 3)
        box = (cx-orbit, cy-orbit, cx+orbit, cy+orbit)
        d.arc(box, start, start+sweep, fill=ink, width=max(1, round(3 * s)))
        d.arc(box, start, start+sweep, fill=mix(primary, (231, 248, 255), .45),
              width=max(1, round(1.1 * s)))

    # Clear state, language and a short voice meter replace stacked HUD rings.
    font_state, font_small = _fonts(width, scale)
    word = {"idle": "READY", "listening": "LISTEN", "transcribing": "WORKING",
            "done": "DONE"}.get(state, "READY")
    subtitle = str(language or "AUTO").upper()
    subtitle = {"ARABIC": "AR", "ENGLISH": "EN", "AUTO LANGUAGE": "AUTO"}.get(subtitle, subtitle)
    if state == "transcribing":
        subtitle = "TRANSCRIBING"
    elif state == "done":
        subtitle = "TEXT READY"
    if hover == "close":
        word, subtitle = "CANCEL", "ESC"
    elif hover == "gear":
        word, subtitle = "SETTINGS", "OPEN"
    elif hover == "mic" and state == "listening":
        word, subtitle = "FINISH", "CLICK"
    if width <= 112 and len(subtitle) > 4:
        subtitle = "" if state != "listening" else subtitle[:4]

    def text(value, font, y, fill):
        if value:
            d.text((cx, y), value, anchor="mm", font=font, fill=fill,
                   stroke_width=max(1, round(.9 * scale)), stroke_fill=ink)
    text(word, font_state, cy - 7 * s, (241, 250, 255))
    text(subtitle, font_small, cy + 9 * s, primary)
    for i in range(5):
        x = cx + (i - 2) * 5 * s
        motion = .5 + .5 * math.sin(phase * 2.1 + i * .8)
        bar = (2 + 8 * level * (.4 + .6 * motion)) * s
        if state == "transcribing":
            bar = (2 + 5 * motion) * s
        stroke([(x, cy + 23*s - bar/2), (x, cy + 23*s + bar/2)], primary, 1.5)

    # Utilities retain the existing locations and gain stronger contrast.
    for px, py, name in ((127, 44, "close"), (127, 108, "gear")):
        x, y, r = px*s, py*s, (10 if hover == name else 8)*s
        col = (248, 253, 255) if hover == name else mix(primary, secondary, .45)
        d.ellipse((x-r, y-r, x+r, y+r), fill=(0, 0, 0, 0),
                  outline=ink, width=max(1, round(3*s)))
        d.ellipse((x-r, y-r, x+r, y+r), outline=col, width=max(1, round(s)))
        if name == "close":
            stroke([(x-3*s, y-3*s), (x+3*s, y+3*s)], col, 1.2)
            stroke([(x-3*s, y+3*s), (x+3*s, y-3*s)], col, 1.2)
        else:
            for dy, knob in ((-3, -1), (1, 2), (4, -2)):
                stroke([(x-4*s, y+dy*s), (x+4*s, y+dy*s)], col, 1)
                d.ellipse((x+(knob-1)*s, y+(dy-1)*s,
                           x+(knob+1)*s, y+(dy+1)*s), fill=col)

    img = img.resize((width, height), Image.Resampling.LANCZOS)
    return img


def preview_orb(size=136, background="#101722", state="listening"):
    """Composite the exact HUD artwork with its actual window opacity."""
    orb = render_orb(size, size, state=state, level=.5, phase=1.1, language="EN")
    alpha = orb.getchannel("A").point(lambda value: round(value * 208 / 255))
    result = Image.new("RGB", orb.size, background)
    result.paste(orb, (0, 0), alpha)
    return result
