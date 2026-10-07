"""
Laptop Monitor Bot  -  reports your laptop (Linux or Windows) to Telegram.

Commands you send the bot:
  /status      -> CPU, RAM, disk, battery summary
  /screenshot  -> current screen as a photo
  /disk        -> usage of every drive
  /net         -> current internet status
  /myid        -> shows your chat id (needed once, during setup)
  /help        -> this list

Automatic alerts (need CHAT_ID set in config.py):
  - "Laptop online" when the bot starts
  - Internet lost / restored
  - New file appears in the watched folder (default: Downloads)

Only your own chat id can use the sensitive commands, so nobody who
stumbles onto the bot can screenshot your screen.
"""

import asyncio
import ctypes
import functools
import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime

import psutil
from PIL import ImageGrab
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import NetworkError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config
import pet_settings

IS_WIN = os.name == "nt"

log = logging.getLogger("monitor_bot")


# ----------------------------- helpers -----------------------------

def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def internet_up(host="8.8.8.8", port=53, timeout=3):
    """True if we can open a socket to a public DNS server."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)          # per-socket, not a global default
            s.connect((host, port))
        return True
    except OSError:
        return False


def build_status(bold=True):
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    root = "C:\\" if IS_WIN else "/"
    disk = psutil.disk_usage(root)
    title = "*Laptop status*" if bold else "Laptop status"
    lines = [
        f"{title}  ({datetime.now():%Y-%m-%d %H:%M:%S})",
        f"CPU:  {cpu:.0f}%",
        f"RAM:  {mem.percent:.0f}%   ({human_bytes(mem.used)} / {human_bytes(mem.total)})",
        f"Disk {'C:' if IS_WIN else '/'}:  {disk.percent:.0f}%   ({human_bytes(disk.free)} free)",
    ]
    batt = psutil.sensors_battery()
    if batt is not None:
        plug = "charging" if batt.power_plugged else "on battery"
        lines.append(f"Battery:  {batt.percent:.0f}%  ({plug})")
    return "\n".join(lines)


PS_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _powershell(script, timeout=20):
    """Run a PowerShell snippet, return stdout (or "" if anything goes wrong)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout, **PS_FLAGS,
        )
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _run(cmd, timeout=15):
    """Run a command, return stdout (or "" if it is missing or fails)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, **PS_FLAGS)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _nmcli_rows(*args):
    """nmcli terse output as lists of fields. Terse mode escapes ':' as '\\:'."""
    out = _run(["nmcli", "-t", *args])
    return [[f.replace("\\:", ":") for f in re.split(r"(?<!\\):", line)]
            for line in out.splitlines() if line]


def wifi_info_linux():
    """Which network we're on, from NetworkManager. None if not connected."""
    for name, kind, dev in _nmcli_rows("-f", "NAME,TYPE,DEVICE",
                                       "connection", "show", "--active"):
        if kind in ("loopback", "bridge", "tun") or dev in ("lo", ""):
            continue
        info = {"name": name, "alias": dev}
        if kind.endswith("wireless"):
            for active, ssid, signal, rate in _nmcli_rows(
                    "-f", "ACTIVE,SSID,SIGNAL,RATE", "device", "wifi", "list",
                    "ifname", dev, "--rescan", "no"):
                if active == "yes":
                    info["name"] = ssid or name
                    info["signal"] = f"{signal}%"
                    info["speed"] = rate
                    break
        else:
            try:
                with open(f"/sys/class/net/{dev}/speed") as f:
                    info["speed"] = f"{int(f.read())} Mb/s"
            except (OSError, ValueError):
                pass
        return info
    return None


def wifi_info():
    """Which network we're on.

    Uses Get-NetConnectionProfile because, unlike `netsh wlan show
    interfaces`, it needs neither admin rights nor Location services -- both
    of which Windows 11 demands before it will reveal an SSID.
    Returns a dict, or None if nothing is connected.
    """
    if not IS_WIN:
        return wifi_info_linux()
    out = _powershell(
        "$p = Get-NetConnectionProfile | "
        "Where-Object { $_.IPv4Connectivity -eq 'Internet' } | Select-Object -First 1; "
        "if ($p) { $a = Get-NetAdapter -InterfaceAlias $p.InterfaceAlias "
        "-ErrorAction SilentlyContinue; "
        "[pscustomobject]@{ name=$p.Name; alias=$p.InterfaceAlias; "
        "category=[string]$p.NetworkCategory; speed=[string]$a.LinkSpeed } "
        "| ConvertTo-Json -Compress }"
    )
    if not out:
        return None
    try:
        info = json.loads(out)
    except ValueError:
        return None

    # Signal strength only comes back when Location services is switched on.
    # Without it we just go without, rather than failing the whole report.
    sig = _powershell("(netsh wlan show interfaces | Select-String 'Signal' | Select-Object -First 1).ToString()", timeout=15)
    if sig and ":" in sig:
        info["signal"] = sig.split(":", 1)[1].strip()
    return info


def ip_location():
    """Rough location from the public IP. None if it can't be determined.

    This is the ISP's exit point, not the laptop -- on a mobile carrier it can
    land hundreds of km away. Useful for "which city, has the network changed",
    not for finding a lost machine.
    """
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=12) as r:
            d = json.load(r)
        return {
            "ip": d.get("ip"), "city": d.get("city"), "region": d.get("region"),
            "country": d.get("country"), "loc": d.get("loc"), "org": d.get("org"),
        }
    except Exception:
        pass
    try:  # fallback, a different provider
        url = ("http://ip-api.com/json/?fields=status,country,regionName,city,"
               "lat,lon,isp,query")
        with urllib.request.urlopen(url, timeout=12) as r:
            d = json.load(r)
        if d.get("status") != "success":
            return None
        loc = f"{d.get('lat')},{d.get('lon')}" if d.get("lat") is not None else None
        return {
            "ip": d.get("query"), "city": d.get("city"), "region": d.get("regionName"),
            "country": d.get("country"), "loc": loc, "org": d.get("isp"),
        }
    except Exception:
        return None


def build_where():
    """Plain-text Wi-Fi + location block."""
    lines = []
    w = wifi_info()
    if w:
        lines.append(f"Network:  {w.get('name') or 'unknown'}")
        bits = [b for b in (w.get("alias"), w.get("speed"),
                            w.get("category") and f"{w['category']} network") if b]
        if bits:
            lines.append("  " + "  -  ".join(bits))
        if w.get("signal"):
            lines.append(f"  signal {w['signal']}")
    else:
        lines.append("Network:  not connected")

    if getattr(config, "LOCATION_ENABLED", True):
        g = ip_location()
        if g:
            place = ", ".join(x for x in (g.get("city"), g.get("region"),
                                          g.get("country")) if x)
            lines.append("")
            lines.append("Approx. location (from public IP - city level, often off):")
            if place:
                lines.append(f"  {place}")
            tail = [x for x in (g.get("ip"), g.get("org")) if x]
            if tail:
                lines.append("  " + "  -  ".join(tail))
            if g.get("loc"):
                lines.append(f"  https://www.google.com/maps?q={g['loc']}")
        else:
            lines.append("")
            lines.append("Location:  unavailable (lookup failed)")
    return "\n".join(lines)


STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "app_state.json")
TG_LIMIT = 4096          # Telegram rejects anything longer


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(d):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def app_inventory():
    """Split everything running into on-screen apps, background, and system.

    "On screen" means the process owns a visible top-level window, which is
    what PowerShell's MainWindowTitle reports. Everything else running under
    your own account counts as background; anything under SYSTEM and friends
    is the operating system itself and only gets counted, not listed.

    On Linux, native Wayland windows cannot be listed by another program, so
    an app counts as open if the desktop launched it: GNOME and KDE put each
    one in its own systemd scope (app-gnome-firefox-1234.scope). Window
    titles come from wmctrl, which only sees X11/XWayland windows.
    """
    windowed = _windows_titles() if IS_WIN else _x11_window_titles()
    scopes = {} if IS_WIN else _app_scopes()

    try:
        me = psutil.Process().username()
    except Exception:
        me = None

    open_apps, background, system = {}, {}, 0
    # process_iter goes in pid order, so the first process seen in a scope is
    # normally the one that was launched and gives the entry its name.
    for pr in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        i = pr.info
        pid = i.get("pid")
        name = i.get("name") or "?"
        mi = i.get("memory_info")
        mem = mi.rss if mi else 0
        key = scopes.get(pid) or (pid if pid in windowed else None)
        if key is not None:
            if name in ("bwrap", "flatpak-bwrap") and pid in scopes:
                name = scopes[pid]            # sandbox launcher: use app id
            e = open_apps.setdefault(key, [name, "", 0])
            e[1] = e[1] or windowed.get(pid, "")
            e[2] += mem
        elif me and i.get("username") == me:
            background[name] = background.get(name, 0) + mem
        else:
            system += 1
    # Browsers and Electron apps start a second scope for their helpers, so
    # the same app can turn up twice; fold those together by name.
    merged = {}
    for name, title, mem in open_apps.values():
        e = merged.setdefault(name, [name, "", 0])
        e[1] = e[1] or title
        e[2] += mem
    return {"open": [tuple(e) for e in merged.values()],
            "background": background, "system": system}


def _windows_titles():
    """pid -> main window title, for every process with a visible window."""
    windowed = {}
    out = _powershell(
        "Get-Process | Where-Object { $_.MainWindowTitle -ne '' } | "
        "Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json -Compress")
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, dict):        # one result is not a list
                data = [data]
            for it in data:
                windowed[int(it["Id"])] = str(it.get("MainWindowTitle") or "")
        except (ValueError, KeyError, TypeError):
            pass
    return windowed


def _x11_window_titles():
    """pid -> window title for X11/XWayland windows, via wmctrl if installed."""
    windowed = {}
    for line in _run(["wmctrl", "-lp"]).splitlines():
        parts = line.split(None, 4)       # id, desktop, pid, host, title
        try:
            pid = int(parts[2])
        except (IndexError, ValueError):
            continue
        if pid > 0:
            windowed.setdefault(pid, parts[4] if len(parts) > 4 else "")
    return windowed


_SCOPE_RE = re.compile(r"/app-(?:[a-z]+-)?(.+?)(?:-\d+)?\.scope$")


def _app_scopes():
    """pid -> id of the launched app it belongs to, from its cgroup."""
    scopes = {}
    for pid in psutil.pids():
        try:
            with open(f"/proc/{pid}/cgroup") as f:
                path = f.read().strip().rsplit(":", 1)[-1]
        except OSError:
            continue
        m = _SCOPE_RE.search(path)
        if m:
            scopes[pid] = m.group(1).replace("\\x2d", "-")
    return scopes


def build_app_report():
    """The 5-hourly app report, including what changed since the last one."""
    inv = app_inventory()
    open_names = {n for n, _, _ in inv["open"]}
    now_names = sorted(open_names | set(inv["background"]))

    state = _load_state()
    prev, prev_time = state.get("apps"), state.get("time")

    L = [f"App report  ({datetime.now():%Y-%m-%d %H:%M})"]
    if prev_time:
        L.append(f"changes measured against {prev_time}")
    L.append("")

    what = "has a window on screen" if IS_WIN else "apps on the desktop"
    L.append(f"OPEN - {what}  ({len(inv['open'])})")
    if inv["open"]:
        for name, title, mem in sorted(inv["open"], key=lambda x: -x[2])[:12]:
            title = (title[:40] + "...") if len(title) > 43 else title
            L.append(f"  {name}  ({human_bytes(mem)})")
            if title:
                L.append(f"      {title}")
        if len(inv["open"]) > 12:
            L.append(f"  ... and {len(inv['open']) - 12} more")
    else:
        L.append("  (none)")

    bg = sorted(inv["background"].items(), key=lambda kv: -kv[1])
    L.append("")
    L.append(f"BACKGROUND - running, no window  ({len(bg)})")
    for name, mem in bg[:15]:
        L.append(f"  {name}  ({human_bytes(mem)})")
    if len(bg) > 15:
        L.append(f"  ... and {len(bg) - 15} more")

    L.append("")
    L.append(f"System processes:  {inv['system']}")

    L.append("")
    if prev is None:
        L.append("This is the first report, so there is nothing to compare")
        L.append("against yet. The next one will list what opened and closed.")
    else:
        opened = sorted(set(now_names) - set(prev))
        closed = sorted(set(prev) - set(now_names))
        L.append(f"OPENED since last report  ({len(opened)})")
        L.append("  " + (", ".join(opened[:25]) if opened else "nothing new"))
        L.append("")
        L.append(f"CLOSED since last report  ({len(closed)})")
        L.append("  " + (", ".join(closed[:25]) if closed else "nothing closed"))

    _save_state({"apps": now_names, "time": f"{datetime.now():%Y-%m-%d %H:%M}"})

    text = "\n".join(L)
    if len(text) > TG_LIMIT:
        text = text[:TG_LIMIT - 20].rsplit("\n", 1)[0] + "\n... (truncated)"
    return text


def build_report():
    """The full push: status + network + location, as plain text.

    Deliberately not Markdown -- network and ISP names arrive with characters
    like _ and * in them, and one stray character makes Telegram reject the
    entire message.
    """
    return build_status(bold=False) + "\n\n" + build_where()


def grab_portal(dest, timeout=30):
    """Screenshot through the desktop portal (org.freedesktop.portal.Screenshot).

    This is the one route recent GNOME still allows: its private screenshot
    interface is locked to GNOME's own tools, which is why gnome-screenshot
    fails there. The portal saves the picture under ~/Pictures itself; it is
    moved to `dest` straight away. Returns True on success.

    Needs PyGObject (python3-gobject on Fedora, already installed with GNOME).
    """
    try:
        import uuid
        from gi.repository import Gio, GLib
    except ImportError:
        return False

    # Run on a private main context: this is called from a worker thread and
    # must not touch anything else's event loop.
    ctx = GLib.MainContext()
    ctx.push_thread_default()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        token = "bot" + uuid.uuid4().hex[:12]
        sender = bus.get_unique_name()[1:].replace(".", "_")
        request = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        loop = GLib.MainLoop(ctx)
        result = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            result["code"], result["res"] = params.unpack()
            loop.quit()

        # Subscribe before calling, so a fast reply cannot be missed.
        sub = bus.signal_subscribe(
            "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
            "Response", request, None, Gio.DBusSignalFlags.NO_MATCH_RULE,
            on_response)
        try:
            bus.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Screenshot", "Screenshot",
                GLib.Variant("(sa{sv})", ("", {
                    "handle_token": GLib.Variant("s", token),
                    "interactive": GLib.Variant("b", False)})),
                GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE,
                timeout * 1000, None)
            timer = GLib.timeout_source_new_seconds(timeout)
            timer.set_callback(lambda *_: loop.quit())
            timer.attach(ctx)
            loop.run()
            timer.destroy()
        finally:
            bus.signal_unsubscribe(sub)

        uri = (result.get("res") or {}).get("uri")
        if result.get("code") != 0 or not uri:
            return False
        shutil.move(Gio.File.new_for_uri(uri).get_path(), dest)
        return True
    except Exception:
        return False
    finally:
        ctx.pop_thread_default()


# Fallbacks when the portal is unavailable: ask the compositor through one of
# these tools. First one installed wins.
WAYLAND_GRABBERS = (
    ("gnome-screenshot", ["gnome-screenshot", "-f"]),        # GNOME
    ("spectacle", ["spectacle", "-b", "-n", "-f", "-o"]),    # KDE
    ("grim", ["grim"]),                                      # Sway, Hyprland
)


def grab_screenshot():
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    if not IS_WIN and os.environ.get("WAYLAND_DISPLAY"):
        if grab_portal(path):
            return path
        for tool, cmd in WAYLAND_GRABBERS:
            if not shutil.which(tool):
                continue
            try:
                subprocess.run(cmd + [path], capture_output=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                continue
            if os.path.getsize(path) > 0:
                return path
        os.remove(path)
        raise RuntimeError("the screenshot portal refused or is missing, and no "
                           "fallback tool worked (spectacle on KDE, grim on "
                           "wlroots)")
    # Windows, or an X11 session
    ImageGrab.grab().save(path)
    return path


def grab_webcam(index=None, warmup=None):
    """Take one still from the webcam. Returns a file path, or None.

    OpenCV is imported lazily so the bot still runs (minus this feature) if
    opencv-python is not installed. The first few frames are discarded on
    purpose -- a webcam's first frame is usually black or badly exposed
    because gain and exposure have not settled yet.
    """
    try:
        import cv2
    except ImportError:
        return None
    if index is None:
        index = getattr(config, "CAMERA_INDEX", 0)
    if warmup is None:
        warmup = getattr(config, "CAMERA_WARMUP", 8)

    cap = None
    try:
        # CAP_DSHOW is markedly faster to open than the default backend on
        # Windows, V4L2 is the native one on Linux; fall back to the default
        # if that backend is unavailable.
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW if IS_WIN else cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            return None
        frame = None
        for _ in range(max(1, warmup)):
            ok, f = cap.read()
            if ok:
                frame = f
            time.sleep(0.06)
        if frame is None:
            return None
        fd, path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return path
    except Exception:
        return None
    finally:
        if cap is not None:
            cap.release()


def owner_only(func):
    """Ignore commands from anyone who isn't the configured owner.

    Until CHAT_ID is set nobody passes -- an unconfigured bot must not hand
    out screenshots to whoever finds it first. /myid stays open so you can
    still finish setup.
    """
    @functools.wraps(func)
    async def wrapper(update, context):
        if not config.CHAT_ID:
            await update.message.reply_text(
                "Not set up yet. Send /myid and paste the number into "
                "config.py as CHAT_ID, then restart the bot."
            )
            return
        if str(update.effective_chat.id) != str(config.CHAT_ID):
            return
        return await func(update, context)
    return wrapper


# ---------------------------- commands -----------------------------

HELP = (
    "Laptop Monitor Bot\n\n"
    "/status - CPU, RAM, disk, battery\n"
    "/screenshot - photo of the current screen\n"
    "/photo - webcam photo\n"
    "/disk - usage of every drive\n"
    "/net - internet status\n"
    "/where - Wi-Fi network + approximate location\n"
    "/apps - what is running, open and closed\n"
    "/shutdown - shut the laptop down (asks you to confirm)\n"
    "/abort - call off a shutdown\n"
    "\nPet:\n"
    "/pet - current pet settings\n"
    "/setpet - then send a picture to hang as the pet\n"
    "/ropecolor <colour> - e.g. red, hotpink, #ff8800\n"
    "/rope thread|chain - rope style\n"
    "/petreset - back to the default pet\n"
    "/petstop - stop the pet\n"
    "/petstart - start the pet\n\n"
    "/caffeine on|off - keep the laptop from sleeping/locking\n\n"
    "/myid - show your chat id\n"
    "/help - this message"
)


@owner_only
async def cmd_help(update, context):
    await update.message.reply_text(HELP)


@owner_only
async def cmd_status(update, context):
    text = await asyncio.to_thread(build_report)
    await update.message.reply_text(text, disable_web_page_preview=True)


@owner_only
async def cmd_screenshot(update, context):
    await context.bot.send_chat_action(update.effective_chat.id, "upload_photo")
    try:
        path = await asyncio.to_thread(grab_screenshot)
    except Exception as e:
        await update.message.reply_text(f"Couldn't take a screenshot: {e}")
        return
    try:
        with open(path, "rb") as f:
            await update.message.reply_photo(f, caption="Current screen")
    finally:
        os.remove(path)


@owner_only
async def cmd_photo(update, context):
    await context.bot.send_chat_action(update.effective_chat.id, "upload_photo")
    path = await asyncio.to_thread(grab_webcam)
    if not path:
        await update.message.reply_text(
            "Couldn't take a photo - camera busy, disabled, or "
            "opencv-python not installed.")
        return
    try:
        with open(path, "rb") as f:
            await update.message.reply_photo(f, caption="Webcam")
    finally:
        os.remove(path)


@owner_only
async def cmd_disk(update, context):
    rows, seen = [], set()
    for p in psutil.disk_partitions():
        # btrfs subvolumes (/, /home) mount the same device more than once
        if p.device in seen or p.mountpoint.startswith(("/snap/", "/var/lib/")):
            continue
        seen.add(p.device)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        where = p.device if IS_WIN else f"{p.mountpoint}  ({p.device})"
        rows.append(f"{where}  {u.percent:.0f}%  ({human_bytes(u.free)} free)")
    await update.message.reply_text("\n".join(rows) or "No drives found.")


@owner_only
async def cmd_net(update, context):
    up = await asyncio.to_thread(internet_up)
    await update.message.reply_text("Internet: ONLINE" if up else "Internet: OFFLINE")


@owner_only
async def cmd_where(update, context):
    text = await asyncio.to_thread(build_where)
    await update.message.reply_text(text, disable_web_page_preview=True)


# ------------------------- pet from Telegram -------------------------

PET_MAX = 800          # pictures are scaled down to this before cutting out


def cut_out(img):
    """Make a picture's plain background transparent. Returns (image, note).

    Telegram re-encodes photos as JPEG, which has no transparency, so the
    background is flood-filled away from the edges inwards: anything joined
    to the border that is close to the colour where the fill started goes.
    This works for a subject on a plain or near-plain background. A PNG sent
    as a file that already has transparency is left untouched.
    """
    from PIL import ImageDraw
    img = img.convert("RGBA")
    img.thumbnail((PET_MAX, PET_MAX))
    alpha = img.split()[3]
    if alpha.getextrema()[0] < 250:
        note = "kept its own transparency"
    else:
        work = img.copy()
        w, h = work.size
        seeds = ([(x, 0) for x in range(0, w, 8)] + [(x, h - 1) for x in range(0, w, 8)]
                 + [(0, y) for y in range(0, h, 8)] + [(w - 1, y) for y in range(0, h, 8)])
        for xy in seeds:
            if work.getpixel(xy)[3]:            # not already cleared
                ImageDraw.floodfill(work, xy, (0, 0, 0, 0), thresh=40)
        cleared = work.split()[3].histogram()[0] / (w * h)
        if cleared < 0.03:
            note = ("background is not plain enough to remove, so it hangs as "
                    "a rectangle. A PNG with transparency, sent as a file, "
                    "works best")
        elif cleared > 0.97:
            note = "could not tell the subject from the background; kept as is"
        else:
            img, note = work, "background removed"
    box = img.split()[3].getbbox()
    return (img.crop(box) if box else img), note


def pet_preview(img):
    """The cut-out on a checkerboard, so the transparency shows in Telegram."""
    from PIL import Image
    w, h = img.size
    bg = Image.new("RGBA", (w, h), (235, 235, 235, 255))
    tile = Image.new("RGBA", (16, 16), (200, 200, 200, 255))
    for y in range(0, h, 16):
        for x in range((y // 16) % 2 * 16, w, 32):
            bg.paste(tile, (x, y))
    bg.alpha_composite(img)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    bg.convert("RGB").save(path)
    return path


def describe_pet():
    d = pet_settings.load()
    image = d.get("image")
    color = d.get("rope_color")
    return (
        "Pet settings\n"
        f"Picture:  {'custom (sent from Telegram)' if image else 'default (pet.png)'}\n"
        f"Rope:  {d.get('rope', 'default')}\n"
        f"Rope colour:  {'#%02x%02x%02x' % tuple(color) if color else 'default'}\n\n"
        "The hanging pet picks up changes within a few seconds.")


@owner_only
async def cmd_pet(update, context):
    await update.message.reply_text(describe_pet())


@owner_only
async def cmd_setpet(update, context):
    context.user_data["await_pet"] = True
    await update.message.reply_text(
        "Send the picture now.\n"
        "Best: a PNG with a transparent background, sent as a File "
        "(paperclip -> File), so Telegram does not flatten it.\n"
        "A normal photo works too if the background is plain; I will cut it out.")


@owner_only
async def on_pet_image(update, context):
    msg = update.message
    wanted = context.user_data.pop("await_pet", False) or \
        (msg.caption or "").strip().lower().startswith("/setpet")
    if not wanted:
        await msg.reply_text("To use this as the pet, send /setpet first, "
                             "or put /setpet in the caption.")
        return
    from PIL import Image
    import io
    tg_file = await (msg.photo[-1] if msg.photo else msg.document).get_file()
    data = await tg_file.download_as_bytearray()
    try:
        src = Image.open(io.BytesIO(bytes(data)))
        src.load()
    except Exception:
        await msg.reply_text("That file is not a picture I can open.")
        return
    img, note = await asyncio.to_thread(cut_out, src)
    await asyncio.to_thread(img.save, pet_settings.CUSTOM_IMAGE)
    pet_settings.update(image=pet_settings.CUSTOM_IMAGE)
    path = await asyncio.to_thread(pet_preview, img)
    try:
        with open(path, "rb") as f:
            await msg.reply_photo(f, caption=f"New pet set - {note}.")
    finally:
        os.remove(path)


@owner_only
async def cmd_ropecolor(update, context):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(
            "Usage: /ropecolor red   (or a name like hotpink, or #ff8800)\n"
            "/ropecolor default - back to gold/silver")
        return
    if text.lower() in ("default", "reset"):
        pet_settings.update(rope_color=None)
        await update.message.reply_text("Rope colour back to default.")
        return
    try:
        rgb = pet_settings.parse_color(text)
    except ValueError:
        await update.message.reply_text(
            f"'{text}' is not a colour I know. Try a name (red, gold, "
            "deepskyblue) or a hex code like #ff8800.")
        return
    pet_settings.update(rope_color=list(rgb))
    await update.message.reply_text(f"Rope colour set to #{'%02x%02x%02x' % rgb}.")


@owner_only
async def cmd_rope(update, context):
    style = (context.args[0].lower() if context.args else "")
    if style not in pet_settings.ROPES:
        await update.message.reply_text("Usage: /rope thread   or   /rope chain")
        return
    pet_settings.update(rope=style)
    await update.message.reply_text(f"Rope set to {style}.")


@owner_only
async def cmd_petreset(update, context):
    pet_settings.update(image=None, rope=None, rope_color=None)
    try:
        os.remove(pet_settings.CUSTOM_IMAGE)
    except OSError:
        pass
    await update.message.reply_text("Pet back to default.")


# Keep in sync with run.sh / run.bat's default.
PET_SCRIPT = "hanging_pet.py"
PET_SCRIPTS = ("hanging_pet.py", "desktop_pet.py", "desktop_pet_classic.py")


def find_pet_process():
    """The running pet process (whichever variant), or None."""
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = p.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(os.path.basename(a) in PET_SCRIPTS for a in cmdline):
            return p
    return None


PET_UNIT = "laptop-pet.service"


def _pet_unit_installed():
    return not IS_WIN and _run(
        ["systemctl", "--user", "list-unit-files", PET_UNIT, "--no-legend"]) != ""


def do_pet_stop():
    # Stop the unit too, or systemd's Restart= would bring the pet back.
    if _pet_unit_installed():
        was = _run(["systemctl", "--user", "is-active", PET_UNIT]) == "active"
        subprocess.run(["systemctl", "--user", "stop", PET_UNIT],
                       capture_output=True, timeout=15)
        if was:
            return True
    p = find_pet_process()
    if not p:
        return False
    p.terminate()
    try:
        p.wait(timeout=5)
    except psutil.TimeoutExpired:
        p.kill()
    return True


def do_pet_start():
    if find_pet_process():
        return False
    if _pet_unit_installed():
        r = subprocess.run(["systemctl", "--user", "start", PET_UNIT],
                           capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or f"exit {r.returncode}")
        return True
    script =os.path.join(os.path.dirname(os.path.abspath(__file__)), PET_SCRIPT)
    if IS_WIN:
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pythonw if os.path.exists(pythonw) else sys.executable
        subprocess.Popen([exe, script], creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.Popen([sys.executable, script],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          start_new_session=True)
    return True


@owner_only
async def cmd_petstop(update, context):
    stopped = await asyncio.to_thread(do_pet_stop)
    await update.message.reply_text(
        "Pet stopped." if stopped else "Pet wasn't running.")


@owner_only
async def cmd_petstart(update, context):
    try:
        started = await asyncio.to_thread(do_pet_start)
    except (OSError, RuntimeError) as e:
        await update.message.reply_text(f"Couldn't start the pet: {e}")
        return
    await update.message.reply_text(
        "Pet started." if started else "Pet is already running.")


# --------------------------- caffeine (stay awake) ---------------------------

_caffeine_proc = None   # Linux: the systemd-inhibit holding the lock
_caffeine_on = False    # Windows: mirrors the last state we told the OS


def do_caffeine(on):
    """Prevent (or allow) the laptop from sleeping / locking. Returns whether
    the state actually changed."""
    global _caffeine_proc, _caffeine_on
    if IS_WIN:
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
        changed = _caffeine_on != on
        _caffeine_on = on
        return changed

    running = _caffeine_proc is not None and _caffeine_proc.poll() is None
    if on:
        if running:
            return False
        _caffeine_proc = subprocess.Popen(
            ["systemd-inhibit", "--what=sleep:idle:handle-lid-switch",
             "--who=Laptop Monitor Bot", "--why=Telegram /caffeine on",
             "sleep", "infinity"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _caffeine_on = True
        return True
    if not running:
        _caffeine_on = False
        return False
    _caffeine_proc.terminate()
    try:
        _caffeine_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _caffeine_proc.kill()
    _caffeine_proc = None
    _caffeine_on = False
    return True


@owner_only
async def cmd_caffeine(update, context):
    arg = (context.args[0].lower() if context.args else "")
    if arg not in ("on", "off"):
        state = "ON" if _caffeine_on else "OFF"
        await update.message.reply_text(
            f"Caffeine is {state}.\nUsage: /caffeine on   or   /caffeine off")
        return
    try:
        changed = await asyncio.to_thread(do_caffeine, arg == "on")
    except FileNotFoundError:
        await update.message.reply_text("systemd-inhibit not found - can't keep the laptop awake.")
        return
    if arg == "on":
        await update.message.reply_text(
            "Caffeine ON - won't sleep, idle-lock, or suspend on lid close."
            if changed else "Already ON.")
    else:
        await update.message.reply_text(
            "Caffeine OFF - normal power/sleep behaviour restored."
            if changed else "Already OFF.")


async def cmd_myid(update, context):
    # deliberately NOT owner-only, so you can discover your id during setup
    await update.message.reply_text(
        f"Your chat id is: {update.effective_chat.id}\n"
        "Paste it into config.py as CHAT_ID, then restart the bot."
    )


# ------------------------ background alerts ------------------------

async def check_internet(context: ContextTypes.DEFAULT_TYPE):
    up = await asyncio.to_thread(internet_up)
    prev = context.bot_data.get("net_up")
    context.bot_data["net_up"] = up
    if prev is not None and up != prev:
        msg = "Internet restored." if up else "Internet lost."
        await context.bot.send_message(config.CHAT_ID, msg)


async def hourly_report(context: ContextTypes.DEFAULT_TYPE):
    text = await asyncio.to_thread(build_report)
    await context.bot.send_message(config.CHAT_ID, text,
                                   disable_web_page_preview=True)


class NewFileHandler(FileSystemEventHandler):
    def __init__(self, loop, bot):
        self.loop = loop
        self.bot = bot

    def on_created(self, event):
        if event.is_directory:
            return
        name = os.path.basename(event.src_path)
        # skip browsers' half-finished download files
        if name.endswith((".crdownload", ".tmp", ".part", ".partial")):
            return
        asyncio.run_coroutine_threadsafe(
            self.bot.send_message(config.CHAT_ID, f"New file: {name}"),
            self.loop,
        )


def _send_now(text, timeout=4):
    """Fire off one Telegram message synchronously, from any thread.

    Used by the shutdown guard, which runs outside the event loop and has
    only a couple of seconds before Windows kills the process.
    """
    try:
        data = urllib.parse.urlencode(
            {"chat_id": config.CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage",
            data, timeout=timeout)
    except Exception:
        pass


def shutdown_delay_text():
    """The wait before a /shutdown takes effect, as the user will see it.

    Linux's shutdown only schedules in whole minutes, so the configured
    seconds are rounded up there.
    """
    if IS_WIN:
        return f"{config.SHUTDOWN_DELAY}s"
    return f"{max(1, math.ceil(config.SHUTDOWN_DELAY / 60))} min"


def do_shutdown(delay):
    if IS_WIN:
        cmd = ["shutdown", "/s", "/t", str(delay), "/c",
               "Shutdown approved from Telegram."]
    else:
        # systemd's shutdown asks logind, which lets the user of the active
        # local session power off without sudo.
        cmd = ["shutdown", "-h", f"+{max(1, math.ceil(delay / 60))}",
               "Shutdown approved from Telegram."]
    return subprocess.run(cmd, capture_output=True, text=True, **PS_FLAGS)


LINUX_SCHEDULED = "/run/systemd/shutdown/scheduled"


def do_abort():
    if IS_WIN:
        return subprocess.run(["shutdown", "/a"], capture_output=True,
                              text=True, **PS_FLAGS)
    if not os.path.exists(LINUX_SCHEDULED):
        # `shutdown -c` succeeds even with nothing to cancel
        return subprocess.CompletedProcess([], 1, "", "nothing scheduled")
    return subprocess.run(["shutdown", "-c"], capture_output=True, text=True)


@owner_only
async def cmd_shutdown(update, context):
    inv = await asyncio.to_thread(app_inventory)
    batt = psutil.sensors_battery()
    bits = [f"{len(inv['open'])} apps open on screen"]
    if batt is not None:
        bits.append(f"battery {batt.percent:.0f}%")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Confirm shutdown", callback_data="sd:yes"),
        InlineKeyboardButton("Cancel", callback_data="sd:no"),
    ]])
    await update.message.reply_text(
        "Shut down this laptop?\n" + "  -  ".join(bits) +
        f"\n\nUnsaved work in those apps may be lost."
        f"\nYou get {shutdown_delay_text()} to send /abort afterwards.",
        reply_markup=kb)


async def on_button(update, context):
    """Handle the Confirm / Cancel buttons."""
    q = update.callback_query
    # Buttons carry their own authorisation check -- owner_only guards
    # messages, not callbacks.
    if not config.CHAT_ID or str(q.message.chat.id) != str(config.CHAT_ID):
        await q.answer("Not allowed.", show_alert=True)
        return
    await q.answer()
    if q.data == "sd:no":
        await q.edit_message_text("Cancelled. Nothing was shut down.")
        return
    if q.data == "sd:yes":
        r = await asyncio.to_thread(do_shutdown, config.SHUTDOWN_DELAY)
        if r.returncode == 0:
            await q.edit_message_text(
                f"Approved. Shutting down in {shutdown_delay_text()}.\n"
                "Send /abort now if you change your mind.")
        else:
            await q.edit_message_text(
                "Shutdown command failed:\n"
                + ((r.stderr or r.stdout).strip()[:300] or f"exit {r.returncode}"))


@owner_only
async def cmd_abort(update, context):
    r = await asyncio.to_thread(do_abort)
    if r.returncode == 0:
        await update.message.reply_text("Shutdown aborted.")
    else:
        await update.message.reply_text(
            "Nothing to abort - no shutdown was in progress.")


@owner_only
async def cmd_apps(update, context):
    text = await asyncio.to_thread(build_app_report)
    await update.message.reply_text(text)


async def app_report_job(context: ContextTypes.DEFAULT_TYPE):
    text = await asyncio.to_thread(build_app_report)
    await context.bot.send_message(config.CHAT_ID, text)


_CTRL_HANDLER = None      # must stay referenced or it gets collected


def install_shutdown_guard():
    """Tell Telegram when Windows starts shutting this laptop down.

    Windows delivers CTRL_SHUTDOWN_EVENT to console programs and then gives
    them only a few seconds, so this sends one synchronous message and gets
    out of the way. It cannot veto the shutdown -- see the README for why.
    """
    global _CTRL_HANDLER
    if not config.CHAT_ID:
        return
    if not IS_WIN:
        # systemd stops the session with SIGTERM, which ends run_polling and
        # runs on_shutdown -- that is where the Linux notice is sent.
        log.info("Shutdown guard armed (reports on exit if the system is stopping).")
        return
    CTRL_LOGOFF, CTRL_SHUTDOWN = 5, 6

    def handler(event):
        if event in (CTRL_LOGOFF, CTRL_SHUTDOWN):
            what = "Log-off" if event == CTRL_LOGOFF else "Shutdown"
            _send_now(f"{what} started on this laptop at "
                      f"{datetime.now():%Y-%m-%d %H:%M:%S}.\n"
                      "This was started at the machine, not from Telegram.")
        return False        # let Windows carry on

    try:
        _CTRL_HANDLER = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_CTRL_HANDLER, True)
        log.info("Shutdown guard armed.")
    except Exception as e:
        log.warning("Could not arm shutdown guard: %s", e)


# --------------------------- lifecycle -----------------------------

async def login_snapshot(app):
    """On startup, send a screen grab and a webcam still of who's here."""
    try:
        shot = await asyncio.to_thread(grab_screenshot)
        try:
            with open(shot, "rb") as f:
                await app.bot.send_photo(config.CHAT_ID, f,
                                         caption="Screen at startup")
        finally:
            os.remove(shot)
    except Exception as e:
        log.warning("Startup screenshot failed: %s", e)
    try:
        cam = await asyncio.to_thread(grab_webcam)
        if cam:
            try:
                with open(cam, "rb") as f:
                    await app.bot.send_photo(config.CHAT_ID, f,
                                             caption="Webcam at startup")
            finally:
                os.remove(cam)
        else:
            await app.bot.send_message(
                config.CHAT_ID, "Startup webcam photo unavailable "
                "(camera busy or opencv-python not installed).")
    except Exception as e:
        log.warning("Startup webcam failed: %s", e)


async def on_startup(app):
    if config.CHAT_ID:
        try:
            await app.bot.send_message(config.CHAT_ID, "Laptop online - bot started.")
        except Exception as e:
            log.warning("Could not send startup message: %s", e)
        if getattr(config, "LOGIN_SNAPSHOT", False):
            await login_snapshot(app)

    # start watching the folder (only if it exists and we know where to send)
    if config.CHAT_ID and os.path.isdir(config.WATCH_FOLDER):
        loop = asyncio.get_running_loop()
        observer = Observer()
        observer.schedule(NewFileHandler(loop, app.bot), config.WATCH_FOLDER,
                          recursive=False)
        observer.start()
        app.bot_data["observer"] = observer
        log.info("Watching folder: %s", config.WATCH_FOLDER)


def linux_power_event():
    """'Shutdown' or 'Reboot' if systemd is taking the machine down, else None."""
    if _run(["systemctl", "is-system-running"], timeout=3) != "stopping":
        return None
    jobs = _run(["systemctl", "list-jobs", "--no-legend"], timeout=3)
    return "Reboot" if ("reboot.target" in jobs or "kexec.target" in jobs) \
        else "Shutdown"


async def on_shutdown(app):
    obs = app.bot_data.get("observer")
    if obs:
        obs.stop()
        obs.join(timeout=2)
    if not IS_WIN and config.CHAT_ID:
        what = linux_power_event()
        if what:
            _send_now(f"{what} started on this laptop at "
                      f"{datetime.now():%Y-%m-%d %H:%M:%S}.")


def wait_for_telegram(poll=5):
    """Block until api.telegram.org can be looked up and reached.

    At login the desktop starts this before Wi-Fi has connected, and the
    first Telegram call failing would end the bot for the whole session.
    """
    waited = 0
    while True:
        try:
            socket.create_connection(("api.telegram.org", 443), timeout=5).close()
            if waited:
                log.info("Network up after %ss.", waited)
            return
        except OSError:
            if waited == 0:
                log.info("Waiting for the network...")
            time.sleep(poll)
            waited += poll


async def on_error(update, context):
    err = context.error
    # Wi-Fi drops and Telegram hiccups are routine; PTB retries on its own.
    if isinstance(err, NetworkError):
        log.warning("Telegram network error: %s", err)
        return
    log.error("Unhandled error", exc_info=err)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")
    # httpx logs every poll at INFO, with the bot token in the URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


EX_CONFIG = 78    # the service unit's RestartPreventExitStatus


def _config_error(msg):
    log.critical(msg)
    sys.exit(EX_CONFIG)


def check_config():
    if not config.BOT_TOKEN or "PASTE" in config.BOT_TOKEN:
        _config_error("Set BOT_TOKEN in config.py first (get it from @BotFather).")
    for name in ("NET_CHECK_INTERVAL", "SHUTDOWN_DELAY"):
        v = getattr(config, name, None)
        if not isinstance(v, int) or v <= 0:
            _config_error(f"config.py: {name} must be a positive whole number.")
    if not IS_WIN:
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
        if os.stat(cfg).st_mode & 0o077:
            log.warning("config.py (holds your bot token) is readable by other "
                        "users. Fix with: chmod 600 %s", cfg)


def main():
    setup_logging()
    check_config()
    log.info("starting")
    wait_for_telegram()

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler(["start", "help"], cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("screenshot", cmd_screenshot))
    app.add_handler(CommandHandler("photo", cmd_photo))
    app.add_handler(CommandHandler("disk", cmd_disk))
    app.add_handler(CommandHandler("net", cmd_net))
    app.add_handler(CommandHandler("where", cmd_where))
    app.add_handler(CommandHandler("apps", cmd_apps))
    app.add_handler(CommandHandler("shutdown", cmd_shutdown))
    app.add_handler(CommandHandler("abort", cmd_abort))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("pet", cmd_pet))
    app.add_handler(CommandHandler("setpet", cmd_setpet))
    app.add_handler(CommandHandler("ropecolor", cmd_ropecolor))
    app.add_handler(CommandHandler("rope", cmd_rope))
    app.add_handler(CommandHandler("petreset", cmd_petreset))
    app.add_handler(CommandHandler("petstop", cmd_petstop))
    app.add_handler(CommandHandler("petstart", cmd_petstart))
    app.add_handler(CommandHandler("caffeine", cmd_caffeine))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE,
                                   on_pet_image))
    app.add_error_handler(on_error)

    if config.CHAT_ID:
        app.job_queue.run_repeating(check_internet,
                                    interval=config.NET_CHECK_INTERVAL, first=10)
        if getattr(config, "STATUS_INTERVAL", 0):
            app.job_queue.run_repeating(hourly_report,
                                        interval=config.STATUS_INTERVAL,
                                        first=getattr(config, "STATUS_FIRST", 30))
            log.info("Status report every %ss", config.STATUS_INTERVAL)
        if getattr(config, "APP_REPORT_INTERVAL", 0):
            app.job_queue.run_repeating(app_report_job,
                                        interval=config.APP_REPORT_INTERVAL,
                                        first=90)
            log.info("App report every %ss", config.APP_REPORT_INTERVAL)
    install_shutdown_guard()

    log.info("Bot running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
