"""
Desktop Pet  -  an inflatable white robot that idles in a screen corner.

A transparent, always-on-top overlay (Windows, and Linux under X11 or
XWayland). The robot breathes, blinks,
and waves. Left-click him to make him wave; right-click to close.

Design notes
------------
* Art is drawn with PIL at 4x and downsampled, so it stays crisp at any
  display scaling. A raster image pasted in would go soft on a 150-200%
  display, which is what most laptops run at now.
* Frames are pre-rendered once at startup and then cycled, because doing the
  4x render inside the animation loop cannot hold 25 fps.
* Windows only gives a borderless window one kind of transparency: a colour
  key. That cannot express partial alpha, so the silhouette is matted against
  the outline grey and then hard-thresholded -- edge pixels read as a soft
  rim rather than as coloured fringing.
* Linux Tk has no colour key at all, so there the window is cut to the same
  hard-edged silhouette with the X11 Shape extension (see x11_shape.py).

Tweak the constants below to change size, corner, and timing.
"""

import ctypes
import math
import os
import sys
import tkinter as tk

from PIL import Image, ImageDraw, ImageFilter, ImageTk

IS_WIN = sys.platform == "win32"

# ------------------------------- settings -------------------------------

CORNER = "top-right"   # top-right | bottom-right | top-left | bottom-left
WIDTH = 150            # robot box width, in logical pixels
HEIGHT = 200           # robot box height
MARGIN = 24            # gap from the screen edges
FRAME_MS = 40          # ~25 fps

BREATH_FRAMES = 14     # frames in one breathe in-and-out cycle
BREATH_DEPTH = 0.035   # how much he squashes, 0.035 = 3.5%
FLOAT_PX = 5           # how far he drifts up and down
BLINK_EVERY = 4.5      # seconds between blinks
BLINK_MS = 140         # how long an eye-blink lasts
WAVE_EVERY = 22        # seconds between unprompted waves (0 = only on click)
WAVE_FRAMES = 16       # frames in one wave

TRANSPARENT = "magenta"   # this exact colour becomes see-through (Windows)

# Optional: drop a PNG next to this file and it is used instead of the
# drawn robot. Transparency is honoured; it still breathes and floats.
IMAGE_FILE = "pet.png"

# Colours
SKIN = (248, 248, 250)
SKIN_SHADE = (222, 223, 231)
OUTLINE = (198, 200, 210)   # dark enough that he stays visible on white
EYE = (26, 26, 32)

SS = 4                 # supersampling factor for the render


# ------------------------------ the artwork ------------------------------

def _capsule(d, p0, p1, width, fill):
    """A line with rounded caps -- PIL has no rotated rounded rectangle."""
    r = width // 2
    d.line([p0, p1], fill=fill, width=width)
    for (x, y) in (p0, p1):
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def draw_robot(squash=1.0, blink=0.0, wave=0.0):
    """Render one frame as RGBA at WIDTH x HEIGHT.

    squash  1.0 = neutral, <1 compressed (and correspondingly wider)
    blink   0.0 = eyes open, 1.0 = fully shut
    wave    0.0 = arm down, 1.0 = arm raised overhead
    """
    W, H = WIDTH * SS, HEIGHT * SS
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    ground = 196.0                      # he squashes towards his feet
    stretch = 1.0 / math.sqrt(squash)   # keep the volume looking constant

    def P(x, y):
        """Design coords (150x200) -> supersampled pixels, with squash."""
        x = 75 + (x - 75) * stretch
        y = ground - (ground - y) * squash
        return (x * SS, y * SS)

    def box(x0, y0, x1, y1):
        return [P(x0, y0), P(x1, y1)]

    # ---- body ----
    d.ellipse(box(29, 70, 121, 182), fill=SKIN, outline=OUTLINE, width=2 * SS)

    # ---- arms ----
    # The right arm lifts and swings when waving; the left one just hangs.
    _capsule(d, P(36, 104), P(15, 146), 22 * SS, SKIN)
    if wave > 0:
        # Sweep out to the SIDE, not inward -- a wider arc puts the hand on
        # his own face at the top of the stroke.
        ang = math.radians(-55 - 30 * wave)
        swing = math.sin(wave * math.pi * 3) * 9 * wave     # the waggle
        ax = 114 + math.cos(ang) * 40 + swing
        ay = 104 + math.sin(ang) * 40
        # Blend out of the resting pose so the arm rises instead of popping.
        ex = 135 + (ax - 135) * wave
        ey = 146 + (ay - 146) * wave
        _capsule(d, P(114, 104), P(ex, ey), 22 * SS, SKIN)
    else:
        _capsule(d, P(114, 104), P(135, 146), 22 * SS, SKIN)

    # ---- legs ----
    _capsule(d, P(58, 166), P(57, 190), 28 * SS, SKIN)
    _capsule(d, P(92, 166), P(93, 190), 28 * SS, SKIN)

    # ---- soft shading, to read as inflated vinyl rather than a flat blob ----
    # Fill the whole frame, then erase an offset copy of the body: what is
    # left is a crescent hugging the lower-right, which is what gives the
    # impression of a rounded, inflated surface.
    shade = Image.new("RGBA", (W, H), SKIN_SHADE + (120,))
    sd = ImageDraw.Draw(shade)
    sd.ellipse(box(21, 58, 113, 170), fill=(0, 0, 0, 0))
    shade = shade.filter(ImageFilter.GaussianBlur(10 * SS))
    body_mask = img.split()[3]                       # only shade actual body
    img.alpha_composite(Image.composite(
        shade, Image.new("RGBA", (W, H), (0, 0, 0, 0)), body_mask))

    # ---- head, drawn after shading so it stays clean ----
    d = ImageDraw.Draw(img)
    d.ellipse(box(41, 22, 109, 74), fill=SKIN, outline=OUTLINE, width=2 * SS)

    # ---- face: two dots joined by a line ----
    eye_r = 5.4
    open_h = eye_r * (1.0 - blink)
    d.line([P(63, 50), P(87, 50)], fill=EYE, width=max(1, int(3.1 * SS)))
    for ex in (63, 87):
        if open_h < 0.6:                              # shut: just the line
            continue
        d.ellipse(box(ex - eye_r, 50 - open_h, ex + eye_r, 50 + open_h),
                  fill=EYE)

    return img.resize((WIDTH, HEIGHT), Image.LANCZOS)


def key_matte(rgba, key):
    """Flatten RGBA onto the colour key so Windows can make it see-through.

    Partially transparent edge pixels are matted against the outline grey
    first, so the rim reads as a soft outline instead of a magenta halo.
    """
    rim = Image.new("RGBA", rgba.size, OUTLINE + (255,))
    soft = Image.alpha_composite(rim, rgba)
    alpha = rgba.split()[3].point(lambda a: 255 if a > 96 else 0)
    out = Image.new("RGB", rgba.size, key)
    out.paste(soft.convert("RGB"), (0, 0), alpha)
    return out


def load_image_frames():
    """Use a user-supplied PNG if one sits next to this script."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), IMAGE_FILE)
    if not os.path.exists(path):
        return None
    src = Image.open(path).convert("RGBA")
    src.thumbnail((WIDTH * SS, HEIGHT * SS), Image.LANCZOS)
    frames = []
    for i in range(BREATH_FRAMES):
        squash = 1.0 - BREATH_DEPTH * (1 - math.cos(2 * math.pi * i / BREATH_FRAMES)) / 2
        w = max(1, int(src.width / math.sqrt(squash)))
        h = max(1, int(src.height * squash))
        sc = src.resize((w, h), Image.LANCZOS)
        canvas = Image.new("RGBA", (WIDTH * SS, HEIGHT * SS), (0, 0, 0, 0))
        canvas.paste(sc, ((canvas.width - w) // 2, canvas.height - h), sc)
        frames.append(canvas.resize((WIDTH, HEIGHT), Image.LANCZOS))
    print(f"Using {IMAGE_FILE}")
    return {"breath": frames, "blink": frames, "wave": []}


def build_frames():
    """Pre-render every frame we will ever need."""
    custom = load_image_frames()
    if custom:
        return custom

    breath, blink, wave = [], [], []
    for i in range(BREATH_FRAMES):
        s = 1.0 - BREATH_DEPTH * (1 - math.cos(2 * math.pi * i / BREATH_FRAMES)) / 2
        breath.append(draw_robot(squash=s))
        blink.append(draw_robot(squash=s, blink=1.0))
    for i in range(WAVE_FRAMES):
        w = math.sin(math.pi * i / (WAVE_FRAMES - 1))     # up then back down
        wave.append(draw_robot(squash=1.0, wave=w))
    return {"breath": breath, "blink": blink, "wave": wave}


# ------------------------------ the window ------------------------------

def work_area(shape=None):
    """Usable desktop, i.e. excluding the taskbar / top bar."""
    if shape is not None:
        area = shape.work_area()
        if area:
            return area

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
    r = RECT()
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
        if r.right > r.left and r.bottom > r.top:
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    root = tk._default_root
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class Pet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()                              # hide while we build
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.shape = None
        if IS_WIN:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        else:
            from x11_shape import XShape
            self.shape = XShape()

        print("Drawing frames...")
        self.frames = build_frames()
        self.photos = {
            k: [ImageTk.PhotoImage(key_matte(f, TRANSPARENT)) for f in v]
            for k, v in self.frames.items()
        }
        self.masks = {
            k: [self.shape.mask(f) for f in v] for k, v in self.frames.items()
        } if self.shape else None
        self.mask = None

        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()
        self.item = self.canvas.create_image(0, 0, anchor="nw",
                                             image=self.photos["breath"][0])

        self.canvas.bind("<Button-1>", lambda e: self.start_wave())
        self.canvas.bind("<Button-3>", lambda e: self.root.destroy())

        self.left, self.top, self.right, self.bottom = work_area(self.shape)
        self.base_x, self.base_y = self.place()
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{self.base_x}+{self.base_y}")

        self.tick = 0
        self.blink_until = 0
        self.next_blink = BLINK_EVERY * 1000 / FRAME_MS
        self.wave_i = -1
        self.next_wave = WAVE_EVERY * 1000 / FRAME_MS if WAVE_EVERY else None

        self.root.deiconify()
        if self.shape:
            from x11_shape import window_id
            self.wid = window_id(self.root, self.shape)
        self.animate()

    def place(self):
        """Top-left position for the chosen corner."""
        right = "right" in CORNER
        bottom = "bottom" in CORNER
        x = (self.right - WIDTH - MARGIN) if right else (self.left + MARGIN)
        y = (self.bottom - HEIGHT - MARGIN) if bottom else (self.top + MARGIN)
        return int(x), int(y)

    def start_wave(self):
        if self.photos["wave"] and self.wave_i < 0:
            self.wave_i = 0

    def animate(self):
        self.tick += 1

        # pick the frame
        if self.wave_i >= 0:
            kind, i = "wave", self.wave_i
            self.wave_i += 1
            if self.wave_i >= len(self.photos["wave"]):
                self.wave_i = -1
                if self.next_wave is not None:
                    self.next_wave = self.tick + WAVE_EVERY * 1000 / FRAME_MS
        else:
            if self.tick >= self.next_blink:
                self.blink_until = self.tick + max(1, BLINK_MS // FRAME_MS)
                self.next_blink = self.tick + BLINK_EVERY * 1000 / FRAME_MS
            kind = "blink" if self.tick < self.blink_until else "breath"
            i = self.tick % len(self.photos[kind])
            if self.next_wave is not None and self.tick >= self.next_wave:
                self.start_wave()

        self.canvas.itemconfig(self.item, image=self.photos[kind][i])
        if self.masks and self.masks[kind][i] != self.mask:
            self.mask = self.masks[kind][i]
            self.shape.apply(self.wid, self.mask)

        # drift gently up and down
        dy = FLOAT_PX * math.sin(2 * math.pi * self.tick / (BREATH_FRAMES * 2))
        self.root.geometry(f"+{self.base_x}+{int(self.base_y + dy)}")

        self.root.after(FRAME_MS, self.animate)

    def run(self):
        self.root.mainloop()


def main():
    # Per-monitor DPI awareness, so the art is not bitmap-stretched on a
    # scaled display. Must happen before Tk starts. (Windows only.)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    if len(sys.argv) > 1 and sys.argv[1] in (
            "top-right", "bottom-right", "top-left", "bottom-left"):
        globals()["CORNER"] = sys.argv[1]

    pet = Pet()
    print(f"Pet is in the {CORNER}. Left-click = wave, right-click = close.")
    pet.run()


if __name__ == "__main__":
    main()
