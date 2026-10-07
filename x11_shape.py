"""
Window shaping for the desktop pets on Linux.

Tk's "-transparentcolor" only exists on Windows. On Linux the equivalent is
the X11 Shape extension: the window is cut to a 1-bit mask, so everything
outside the silhouette is simply not part of the window -- it is not drawn
and clicks go through it. Tk has no Wayland backend, so on a Wayland desktop
it runs under XWayland, where this works the same way.

Like the Windows colour key, the mask is all-or-nothing per pixel, so the
pets' existing hard-thresholded edges carry over unchanged.

Only libX11 and libXext are needed, both part of any X11/XWayland install.
"""

import ctypes
import ctypes.util

SHAPE_BOUNDING = 0
SHAPE_SET = 0


class XShape:
    def __init__(self):
        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        xext = ctypes.CDLL(ctypes.util.find_library("Xext") or "libXext.so.6")
        vp, ul = ctypes.c_void_p, ctypes.c_ulong
        x11.XOpenDisplay.restype = vp
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XDefaultRootWindow.restype = ul
        x11.XDefaultRootWindow.argtypes = [vp]
        x11.XCreateBitmapFromData.restype = ul
        x11.XCreateBitmapFromData.argtypes = [vp, ul, ctypes.c_char_p,
                                              ctypes.c_uint, ctypes.c_uint]
        x11.XFlush.argtypes = [vp]
        x11.XInternAtom.restype = ul
        x11.XInternAtom.argtypes = [vp, ctypes.c_char_p, ctypes.c_int]
        x11.XGetWindowProperty.argtypes = [
            vp, ul, ul, ctypes.c_long, ctypes.c_long, ctypes.c_int, ul,
            ctypes.POINTER(ul), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ul), ctypes.POINTER(ul),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_long))]
        x11.XFree.argtypes = [vp]
        x11.XQueryTree.argtypes = [vp, ul, ctypes.POINTER(ul), ctypes.POINTER(ul),
                                   ctypes.POINTER(ctypes.POINTER(ul)),
                                   ctypes.POINTER(ctypes.c_uint)]
        x11.XFreePixmap.argtypes = [vp, ul]
        xext.XShapeCombineMask.argtypes = [vp, ul, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ul, ctypes.c_int]
        self.x11, self.xext = x11, xext
        # A connection of our own: X resources live on the server, so a mask
        # set from here applies to Tk's window just the same.
        self.dpy = x11.XOpenDisplay(None)
        if not self.dpy:
            raise OSError("cannot open the X display (is DISPLAY set?)")
        self.root = x11.XDefaultRootWindow(self.dpy)

    def mask(self, image, threshold=96):
        """A server-side 1-bit mask from an RGBA PIL image's alpha channel."""
        alpha = image.split()[3].point(lambda a: 255 if a > threshold else 0)
        # X bitmaps are least-significant-bit first; PIL packs "1" images
        # most-significant first, which "1;R" reverses.
        data = alpha.convert("1").tobytes("raw", "1;R")
        return self.x11.XCreateBitmapFromData(self.dpy, self.root, data,
                                              image.width, image.height)

    def free(self, mask):
        """Release a mask made by mask() once it is no longer needed."""
        self.x11.XFreePixmap(self.dpy, mask)

    def apply(self, window_id, mask, x=0, y=0):
        """Cut a window to a mask made by mask(), placed at (x, y) in it."""
        self.xext.XShapeCombineMask(self.dpy, window_id, SHAPE_BOUNDING,
                                    x, y, mask, SHAPE_SET)
        self.x11.XFlush(self.dpy)

    def toplevel(self, window):
        """Walk up from a window to the ancestor that is a child of the root.

        That outermost window is what the compositor draws, so it is the one
        that has to be shaped: shaping a window inside it still leaves the
        parent painting a full rectangle around it.
        """
        ul = ctypes.c_ulong
        while True:
            root, parent = ul(), ul()
            children, n = ctypes.POINTER(ul)(), ctypes.c_uint()
            if not self.x11.XQueryTree(self.dpy, window, ctypes.byref(root),
                                       ctypes.byref(parent),
                                       ctypes.byref(children), ctypes.byref(n)):
                return window
            if children:
                self.x11.XFree(children)
            if not parent.value or parent.value == root.value:
                return window
            window = parent.value

    def work_area(self):
        """(left, top, right, bottom) of the desktop minus panels, or None.

        Read from _NET_WORKAREA, which the window manager keeps up to date.
        """
        x11, ul = self.x11, ctypes.c_ulong
        atom = x11.XInternAtom(self.dpy, b"_NET_WORKAREA", 1)
        if not atom:
            return None
        rtype, fmt, n, rest = ul(), ctypes.c_int(), ul(), ul()
        data = ctypes.POINTER(ctypes.c_long)()
        ok = x11.XGetWindowProperty(self.dpy, self.root, atom, 0, 4, 0, 0,
                                    ctypes.byref(rtype), ctypes.byref(fmt),
                                    ctypes.byref(n), ctypes.byref(rest),
                                    ctypes.byref(data))
        if ok != 0 or not data:
            return None
        try:
            if fmt.value != 32 or n.value < 4:
                return None
            x, y, w, h = (data[i] for i in range(4))
        finally:
            x11.XFree(data)
        return (x, y, x + w, y + h) if w > 0 and h > 0 else None


def window_id(tk_root, shape):
    """X id of a Tk toplevel's outermost window, which is the one to shape.

    Tk reports its inner window (wm_frame gives the same id for a borderless
    window), so the real top is found by walking up the tree.
    """
    tk_root.update_idletasks()
    return shape.toplevel(int(tk_root.wm_frame(), 16))
