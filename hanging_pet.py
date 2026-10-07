"""
Hanging Pet  -  a charm that dangles from the top of your screen on a rope.

It swings like a real pendulum: grab it and drag it to one side, let go, and
it swings back and forth and slowly settles. Now and then a little breeze
sets it swaying again. Click it for a nudge; right-click closes it.

    python hanging_pet.py                    # pet.png if present, else the robot
    python hanging_pet.py my.png --rope chain --at 0.3

The picture, rope style and rope colour can also be changed from Telegram
(/setpet, /rope, /ropecolor in monitor_bot.py). Those land in
pet_settings.json, which this watches, redrawing itself when it changes.
Command-line options win over those settings.

Design notes
------------
* The rope and the charm move as one rigid piece that pivots at the top, so
  every frame is the same picture rotated about the pivot. All the angles are
  rendered once at startup (1 degree apart) and the animation just picks one.
* Physics is a damped pendulum, theta'' = -(g/L) sin(theta) - c * theta',
  stepped once per frame.
* Transparency works the same as desktop_pet.py: a colour key on Windows, the
  X11 Shape extension on Linux (x11_shape.py). Both are all-or-nothing per
  pixel, so edge pixels are matted against a dark rim, which suits the
  black outlines of most cartoon art.
"""

import argparse
import math
import os
import random
import sys
import threading
import tkinter as tk

import pet_settings

from PIL import Image, ImageDraw, ImageTk

IS_WIN = sys.platform == "win32"

# ------------------------------- settings -------------------------------

CHARM_W = 120          # charm is scaled to fit this box (logical pixels)
CHARM_H = 150
ROPE_LEN = 90          # pixels of rope above the charm
ROPE = "thread"        # thread | chain
ANCHOR_AT = 0.82       # where along the top edge it hangs, 0 = left, 1 = right
FRAME_MS = 16          # ~60 fps

MAX_ANGLE = 70         # furthest it can swing, in degrees either side
SWING_PERIOD = 1.7     # seconds for one full swing there and back
DAMPING = 0.45         # higher = settles sooner
BREEZE_EVERY = 9       # average seconds between gentle breezes (0 = never)
NUDGE = 1.6            # how hard a click pushes it (radians per second)

TRANSPARENT = "magenta"     # colour-key for Windows; hidden by the mask on Linux
RIM = (28, 28, 32)          # edge pixels are matted against this
GOLD = (212, 170, 64)
GOLD_DARK = (150, 112, 30)
SILVER = (196, 200, 210)
SILVER_DARK = (110, 114, 124)

IMAGE_FILE = "pet.png"

# ------------------------------- artwork --------------------------------


def load_charm(path):
    """The charm art: a PNG if given or present, else the robot from desktop_pet."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = path or os.path.join(here, IMAGE_FILE)
    if path and os.path.exists(path):
        img = Image.open(path).convert("RGBA")
        img = img.crop(img.split()[3].getbbox() or (0, 0, *img.size))
    else:
        from desktop_pet import draw_robot
        img = draw_robot()
    img.thumbnail((CHARM_W, CHARM_H), Image.LANCZOS)
    return img


def shade(rgb, f=0.62):
    """A darker version of a colour, for outlines."""
    return tuple(int(c * f) for c in rgb)


def draw_rope(d, x, y0, y1, style, color=None):
    """Rope hanging straight down from (x, y0) to (x, y1)."""
    if style == "chain":
        silver = tuple(color) if color else SILVER
        silver_dark = shade(silver) if color else SILVER_DARK
        link_h, y, i = 12, y0, 0
        while y < y1:
            if i % 2 == 0:      # link seen face-on: an open oval
                d.ellipse([x - 5, y - 1, x + 5, y + link_h + 1],
                          outline=silver_dark, width=4)
                d.ellipse([x - 4, y, x + 4, y + link_h], outline=silver, width=2)
            else:               # link seen edge-on: a thin bar
                d.rounded_rectangle([x - 2, y, x + 2, y + link_h], radius=2,
                                    fill=silver, outline=silver_dark)
            y += link_h - 3
            i += 1
    else:
        gold = tuple(color) if color else GOLD
        gold_dark = shade(gold) if color else GOLD_DARK
        d.line([x, y0, x, y1], fill=gold_dark, width=4)
        d.line([x, y0, x, y1], fill=gold, width=2)
    # the knot it hangs from
    knot = tuple(color) if color else GOLD
    knot_dark = shade(knot) if color else GOLD_DARK
    d.ellipse([x - 4, y0 - 4, x + 4, y0 + 4], fill=knot_dark)
    d.ellipse([x - 2, y0 - 2, x + 2, y0 + 2], fill=knot)


def attach_depth(charm):
    """How far down the charm's centre line its art actually starts.

    Cut-out art rarely fills the top middle of its box (a raised arm, a
    rounded head), so the rope is run down to the first solid pixel near the
    centre instead of stopping in mid-air.
    """
    alpha = charm.split()[3]
    cx = charm.width // 2
    for y in range(charm.height):
        if any(alpha.getpixel((x, y)) > 128
               for x in range(max(0, cx - 3), min(charm.width, cx + 4))):
            return y
    return 0


def build_strip(charm, style, color=None):
    """Rope + charm hanging straight down, pivot at the top centre."""
    w = max(charm.width, 16)
    top = ROPE_LEN + 6                  # where the charm's box starts
    strip = Image.new("RGBA", (w, top + charm.height), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    cx = w // 2
    ry = top + attach_depth(charm)      # where the rope meets the art
    draw_rope(d, cx, 4, ry - 4, style, color)
    strip.alpha_composite(charm, ((w - charm.width) // 2, top))
    # a small ring where rope meets charm, drawn over the art's edge
    ring = tuple(color) if color else GOLD
    ring_dark = shade(ring) if color else GOLD_DARK
    d = ImageDraw.Draw(strip)
    d.ellipse([cx - 6, ry - 10, cx + 6, ry + 2], outline=ring_dark, width=4)
    d.ellipse([cx - 5, ry - 9, cx + 5, ry + 1], outline=ring, width=2)
    return strip, (cx, 4)


def keyed(rgba):
    """Hard-edged RGB for display, plus the 0/255 mask of what is solid.

    On Windows the see-through part must be exactly the colour key. On Linux
    the mask hides it, but Tk paints over one X connection and the mask is set
    over another, so for a single frame the picture can lag the outline; the
    hidden part is filled with the dark rim so that lag reads as outline, not
    as a magenta flash.
    """
    rim = Image.new("RGBA", rgba.size, RIM + (255,))
    soft = Image.alpha_composite(rim, rgba).convert("RGB")
    if IS_WIN:
        solid = rgba.split()[3].point(lambda a: 255 if a > 96 else 0)
        out = Image.new("RGB", rgba.size, TRANSPARENT)
        out.paste(soft, (0, 0), solid)
        return out, solid
    return soft, rgba.split()[3].point(lambda a: 255 if a > 96 else 0)


def build_frames(strip, pivot):
    """One cropped frame per whole degree, all pivoting about the same point.

    Returns (side, frames): the square window size, and per angle a tuple of
    (keyed RGB image, 1-bit alpha, x offset, y offset) inside that square.
    """
    px, py = pivot
    # Distance from the pivot to the strip's farthest corner bounds every pose.
    reach = math.ceil(max(math.hypot(x - px, y - py)
                          for x in (0, strip.width) for y in (0, strip.height)))
    side = 2 * reach + 4
    c = side // 2
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(strip, (c - px, c - py))

    frames = {}
    for a in range(-MAX_ANGLE, MAX_ANGLE + 1):
        # PIL turns counter-clockwise for positive angles, which swings the
        # charm to the right: the same sign as the physics.
        rot = square.rotate(a, resample=Image.BICUBIC, center=(c, c))
        box = rot.split()[3].getbbox()
        if not box:
            continue
        crop = rot.crop(box)
        img, solid = keyed(crop)
        frames[a] = (img, solid, box[0], box[1])
    return side, frames


# ------------------------------- the window -------------------------------


def resolve(cli):
    """What to draw: command line first, then Telegram's settings, then defaults."""
    saved = pet_settings.load()
    image = cli.image or saved.get("image")
    if image and not os.path.exists(image):
        image = None                         # a deleted custom picture
    rope = cli.rope or saved.get("rope") or ROPE
    if rope not in pet_settings.ROPES:
        rope = ROPE
    color = saved.get("rope_color")
    color = tuple(color) if isinstance(color, list) and len(color) == 3 else None
    return image, rope, color


def render(image, rope, color):
    """All the PIL work for one look; safe to run off the Tk thread."""
    strip, pivot = build_strip(load_charm(image), rope, color)
    return build_frames(strip, pivot)


class HangingPet:
    def __init__(self, cli, anchor_at):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.shape = None
        if IS_WIN:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        else:
            from x11_shape import XShape
            self.shape = XShape()

        self.cli = cli
        self.look = resolve(cli)
        print("Drawing frames...")
        self.side, raw = render(*self.look)
        self.frames = {}
        self.load_frames(raw)

        # On Linux the mask hides the background; dark for the same reason
        # as in keyed().
        self.canvas = tk.Canvas(self.root, width=self.side, height=self.side,
                                bg=TRANSPARENT if IS_WIN else "#%02x%02x%02x" % RIM,
                                highlightthickness=0)
        self.canvas.pack()
        self.item = self.canvas.create_image(0, 0, anchor="nw")

        # Pivot on the top edge of the usable desktop (below GNOME's top bar).
        from desktop_pet import work_area
        left, top, right, _ = work_area(self.shape)
        self.ax = int(left + (right - left) * anchor_at)
        self.ay = int(top)
        self.place()

        # physics state
        self.theta = 0.0            # radians, positive = swung to the right
        self.omega = 0.0            # radians per second
        self.k = (2 * math.pi / SWING_PERIOD) ** 2
        self.dragging = False
        self.press = None
        self.shown = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", lambda e: self.root.destroy())

        self.root.deiconify()
        if self.shape:
            from x11_shape import window_id
            self.wid = window_id(self.root, self.shape)
        # start with a small swing so it is obviously alive
        self.omega = 1.2
        self.tick()

        # watch for changes sent from Telegram
        self.seen = (pet_settings.mtime(), self._image_mtime())
        self.pending = None
        self.root.after(1000, self.watch)

    def place(self):
        half = self.side // 2
        self.root.geometry(f"{self.side}x{self.side}+{self.ax - half}+{self.ay - half}")

    def load_frames(self, raw):
        """Turn rendered frames into Tk images and window masks."""
        old = self.frames
        self.frames = {}
        for a, (img, solid, ox, oy) in raw.items():
            mask = self.shape.mask(_alpha_rgba(solid)) if self.shape else None
            self.frames[a] = (ImageTk.PhotoImage(img), mask, ox, oy)
        return old

    # ---- live changes ----

    def _image_mtime(self):
        try:
            return os.path.getmtime(pet_settings.CUSTOM_IMAGE)
        except OSError:
            return None

    def watch(self):
        """Once a second: has Telegram changed the look? Redraw if so."""
        now = (pet_settings.mtime(), self._image_mtime())
        if now != self.seen and self.pending is None:
            image_changed = now[1] != self.seen[1]  # same path, new picture
            self.seen = now
            look = resolve(self.cli)
            if look != self.look or image_changed:
                self.look = look
                self.pending = {}
                job = self.pending
                # Rotating ~140 frames takes a moment; do it off the Tk thread
                # so the pet keeps swinging meanwhile.
                threading.Thread(target=lambda: job.update(
                    result=render(*look)), daemon=True).start()
        if self.pending is not None and "result" in self.pending:
            side, raw = self.pending["result"]
            self.pending = None
            old = self.load_frames(raw)
            if side != self.side:
                self.side = side
                self.canvas.config(width=side, height=side)
                self.place()
            self.shown = None                       # force a redraw
            self.tick_draw()
            if self.shape:
                for _, mask, _, _ in old.values():
                    self.shape.free(mask)
            print("Pet updated.")
        self.root.after(250 if self.pending is not None else 1000, self.watch)

    # ---- mouse ----

    def pointer_angle(self, e):
        a = math.atan2(e.x_root - self.ax, e.y_root - self.ay)
        lim = math.radians(MAX_ANGLE)
        return max(-lim, min(lim, a))

    def on_press(self, e):
        self.press = (e.x_root, e.y_root)
        self.grab_offset = self.theta - self.pointer_angle(e)

    def on_drag(self, e):
        if not self.press:
            return
        if not self.dragging and math.hypot(e.x_root - self.press[0],
                                            e.y_root - self.press[1]) < 4:
            return
        self.dragging = True
        lim = math.radians(MAX_ANGLE)
        target = max(-lim, min(lim, self.pointer_angle(e) + self.grab_offset))
        dt = FRAME_MS / 1000
        # Smoothed hand speed, so letting go flings it the way it was moving.
        self.omega = 0.6 * self.omega + 0.4 * (target - self.theta) / dt
        self.theta = target

    def on_release(self, e):
        if not self.dragging and self.press:
            # a click, not a drag: push it away from the side that was clicked
            side = 1 if e.x_root < self.ax + math.sin(self.theta) * 100 else -1
            self.omega += side * NUDGE
        self.dragging = False
        self.press = None

    # ---- animation ----

    def tick(self):
        dt = FRAME_MS / 1000
        if not self.dragging:
            # semi-implicit Euler: stable for a pendulum at this step size
            self.omega += (-self.k * math.sin(self.theta)
                           - DAMPING * self.omega) * dt
            self.theta += self.omega * dt
            lim = math.radians(MAX_ANGLE)
            if abs(self.theta) > lim:                 # soft stop at the limit
                self.theta = math.copysign(lim, self.theta)
                self.omega *= -0.3
            if BREEZE_EVERY and random.random() < dt / BREEZE_EVERY:
                self.omega += random.uniform(-0.5, 0.5)
        else:
            self.omega *= 0.9       # hand stops moving: speed decays

        self.tick_draw()
        self.root.after(FRAME_MS, self.tick)

    def tick_draw(self):
        a = int(round(math.degrees(self.theta)))
        a = max(-MAX_ANGLE, min(MAX_ANGLE, a))
        if a != self.shown and a in self.frames:
            self.shown = a
            photo, mask, ox, oy = self.frames[a]
            self.canvas.itemconfig(self.item, image=photo)
            self.canvas.coords(self.item, ox, oy)
            if mask:
                # Paint the new frame first, then cut the window to it, so
                # the mask runs ahead of the picture as little as possible.
                self.root.update_idletasks()
                self.shape.apply(self.wid, mask, ox, oy)

    def run(self):
        self.root.mainloop()


def _alpha_rgba(solid):
    """Wrap a 0/255 mask as RGBA so XShape.mask can read its alpha."""
    img = Image.new("RGBA", solid.size, (0, 0, 0, 0))
    img.putalpha(solid)
    return img


def main():
    ap = argparse.ArgumentParser(description="A charm hanging from the top of the screen.")
    ap.add_argument("image", nargs="?", help=f"PNG to hang (default: {IMAGE_FILE} "
                    "next to this script, else the robot)")
    ap.add_argument("--rope", choices=pet_settings.ROPES, default=None)
    ap.add_argument("--at", type=float, default=ANCHOR_AT,
                    help="where along the top edge, 0 = left, 1 = right")
    args = ap.parse_args()

    pet = HangingPet(args, min(1.0, max(0.0, args.at)))
    print("Hanging. Drag to swing, click to nudge, right-click to close.")
    pet.run()


if __name__ == "__main__":
    main()
