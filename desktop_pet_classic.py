"""
Desktop Pet  -  a little robot that runs across the top of your screen.

Runs as a transparent, always-on-top overlay (Windows, and Linux under X11
or XWayland). Click the robot to close it.

Tweak the constants below to change size / speed / height.
"""

import sys
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

IS_WIN = sys.platform == "win32"

TRANSPARENT = "magenta"   # this exact colour becomes see-through (Windows)
SIZE = 100                # window box the robot is drawn in
SPEED = 6                 # pixels moved per frame (bigger = faster run)
FRAME_MS = 40             # milliseconds per frame (~25 fps)
TOP_MARGIN = 8            # distance from the very top of the screen


def draw_frame(stride):
    """One running pose as RGBA. stride 0/1 picks the leg position and bob.

    Drawn into an image rather than onto the canvas so Linux can cut the
    window to its outline (see x11_shape.py).
    """
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bob = -3 if stride == 0 else 0                   # little vertical bounce
    cx, cy = SIZE // 2, SIZE // 2 + bob
    dark = "#20202a"

    # body
    d.rectangle([cx-22, cy-18, cx+22, cy+18], fill="#4a4a55", outline=dark, width=2)
    # head
    d.rectangle([cx-14, cy-34, cx+14, cy-16], fill="#5b5b6a", outline=dark, width=2)
    # antenna
    d.line([cx, cy-34, cx, cy-42], fill="#8a8aa0", width=2)
    d.ellipse([cx-3, cy-46, cx+3, cy-40], fill="#00e0ff")
    # glowing eyes
    d.ellipse([cx-9, cy-28, cx-3, cy-22], fill="#00e0ff")
    d.ellipse([cx+3, cy-28, cx+9, cy-22], fill="#00e0ff")
    # arms
    d.line([cx-22, cy-6, cx-30, cy+4], fill=dark, width=4)
    d.line([cx+22, cy-6, cx+30, cy+4], fill=dark, width=4)
    # legs - alternate positions to look like running
    if stride == 0:
        d.line([cx-10, cy+18, cx-17, cy+30], fill=dark, width=4)
        d.line([cx+10, cy+18, cx+14, cy+27], fill=dark, width=4)
    else:
        d.line([cx-10, cy+18, cx-14, cy+27], fill=dark, width=4)
        d.line([cx+10, cy+18, cx+17, cy+30], fill=dark, width=4)
    return img


def on_key(rgba):
    """Flatten onto the colour key, which Windows makes see-through."""
    out = Image.new("RGB", rgba.size, TRANSPARENT)
    out.paste(rgba, (0, 0), rgba.split()[3].point(lambda a: 255 if a > 96 else 0))
    return out


class Pet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)                 # no title bar / border
        self.root.attributes("-topmost", True)           # stay above windows
        self.shape = None
        if IS_WIN:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        else:
            from x11_shape import XShape
            self.shape = XShape()

        self.sw = self.root.winfo_screenwidth()
        self.x = -SIZE
        self.y = TOP_MARGIN
        self.step = 0

        frames = [draw_frame(0), draw_frame(1)]
        self.photos = [ImageTk.PhotoImage(on_key(f)) for f in frames]
        self.masks = [self.shape.mask(f) for f in frames] if self.shape else None

        self.root.geometry(f"{SIZE}x{SIZE}+{self.x}+{self.y}")
        self.canvas = tk.Canvas(self.root, width=SIZE, height=SIZE,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()
        self.item = self.canvas.create_image(0, 0, anchor="nw",
                                             image=self.photos[0])
        self.canvas.bind("<Button-1>", lambda e: self.root.destroy())

        if self.shape:
            from x11_shape import window_id
            self.wid = window_id(self.root, self.shape)
        self.stride = None
        self.animate()

    def animate(self):
        self.step += 1
        self.x += SPEED
        if self.x > self.sw:            # ran off the right edge -> loop back
            self.x = -SIZE
        self.root.geometry(f"+{self.x}+{self.y}")
        stride = (self.step // 3) % 2
        if stride != self.stride:
            self.stride = stride
            self.canvas.itemconfig(self.item, image=self.photos[stride])
            if self.masks:
                self.shape.apply(self.wid, self.masks[stride])
        self.root.after(FRAME_MS, self.animate)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Pet().run()
