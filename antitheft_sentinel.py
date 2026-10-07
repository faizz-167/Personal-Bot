"""
Anti-theft sentinel  -  alerts you on Telegram when a login fails.

This is the half of the anti-theft setup that has to run BEFORE anyone logs
in, so it can catch a thief failing your PIN or password at the lock screen.

It is deliberately separate from monitor_bot.py, for two reasons:

  * It is meant to run as a Windows service / scheduled task under the SYSTEM
    account, started at boot. SYSTEM can read the Security event log (where
    failed logins are recorded); a normal program cannot.
  * It only SENDS Telegram messages, it never polls for commands. That means
    it does not clash with monitor_bot.py, which does poll -- Telegram allows
    only one poller per bot token, but any number of senders.

It can run two ways:

  once   - look at the last few minutes for failed logins and report them,
           then exit. This is what the event-triggered scheduled task uses:
           Windows runs it each time a failed-login event is written.
  watch  - stay running and poll the log every few seconds. Useful for
           testing, or if you would rather run it as a plain background task.

On Linux the same job is done from the systemd journal instead: PAM logs
every refused password (login screen, lock screen, sudo, su, SSH, text
console), and root can read it. install_antitheft.sh runs this script in
"watch" mode as a root systemd service, started at boot, so it is armed at the
GDM login screen before anyone logs in.

Screenshots are not attempted here: before login there is no desktop to
capture. A webcam grab IS attempted, because the camera is a separate device
-- but at the lock screen Windows often denies it (Linux does not, when
running as root), so it is best-effort and
its absence is never treated as an error.
"""

import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import config

# Security-log event ids that mean "a logon was refused".
#   4625 - an account failed to log on (the main one: wrong password, and on
#          most machines wrong Windows Hello PIN too)
#   4776 - the computer failed to validate credentials
FAIL_IDS = [4625, 4776]

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "sentinel_seen.json")
IS_WIN = os.name == "nt"
PS_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WIN else {}


def send(text):
    """Send one Telegram message. Best effort; never raises."""
    if not getattr(config, "BOT_TOKEN", "") or "PASTE" in config.BOT_TOKEN:
        return
    if not getattr(config, "CHAT_ID", ""):
        return
    try:
        data = urllib.parse.urlencode(
            {"chat_id": config.CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage",
            data, timeout=8)
    except Exception:
        pass


def send_photo(path, caption):
    """Send a photo via multipart/form-data, without any third-party library."""
    try:
        with open(path, "rb") as f:
            body = f.read()
    except OSError:
        return
    boundary = "----sentinel" + str(int(time.time() * 1000))
    pre = (f"--{boundary}\r\n"
           f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
           f"{config.CHAT_ID}\r\n"
           f"--{boundary}\r\n"
           f'Content-Disposition: form-data; name="caption"\r\n\r\n'
           f"{caption}\r\n"
           f"--{boundary}\r\n"
           f'Content-Disposition: form-data; name="photo"; filename="cam.jpg"\r\n'
           f"Content-Type: image/jpeg\r\n\r\n").encode()
    post = f"\r\n--{boundary}--\r\n".encode()
    payload = pre + body + post
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendPhoto",
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception:
        pass


def try_webcam():
    """Best-effort webcam still. Returns a path or None (often None at the
    lock screen, where Windows blocks camera access)."""
    try:
        import cv2
    except ImportError:
        return None
    idx = getattr(config, "CAMERA_INDEX", 0)
    cap = None
    try:
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW if IS_WIN else cv2.CAP_V4L2)
        if not cap.isOpened():
            return None
        frame = None
        for _ in range(getattr(config, "CAMERA_WARMUP", 8)):
            ok, f = cap.read()
            if ok:
                frame = f
            time.sleep(0.06)
        if frame is None:
            return None
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return path
    except Exception:
        return None
    finally:
        if cap is not None:
            cap.release()


# How the logon was attempted (event field LogonType).
LOGON_TYPES = {
    "2": "at the keyboard", "7": "unlocking the screen",
    "10": "remote desktop", "3": "over the network", "11": "cached login",
}

# Why it was refused (event field SubStatus). These are the codes seen for a
# lock-screen refusal -- notably a wrong Windows Hello PIN is 0xc0000380,
# which is how we can say "wrong PIN" rather than just "login failed".
FAIL_REASONS = {
    "0xc0000380": "wrong PIN",
    "0xc000006a": "wrong password",
    "0xc0000064": "no such user",
    "0xc0000072": "account disabled",
    "0xc0000234": "account locked out",
    "0xc0000193": "account expired",
    "0xc0000070": "not allowed from here",
}


# ---------------------------- Linux (journald) ----------------------------

# What the PAM service name says about where the password was typed.
PAM_SERVICES = {
    "gdm-password": "at the login / lock screen",
    "gdm-fingerprint": "fingerprint at the login / lock screen",
    "gdm-smartcard": "smartcard at the login / lock screen",
    "sddm": "at the login screen", "kde": "at the lock screen",
    "lightdm": "at the login screen", "login": "on a text console",
    "sudo": "running sudo", "su": "running su", "sshd": "over SSH",
    "polkit-1": "at an admin password prompt",
}

# Every refusal -- graphical, console, sudo, SSH -- goes through PAM, which
# logs one line per attempt. (login and sshd log their own lines too, but
# counting those as well would report each attempt twice.)
PAM_FAIL = re.compile(
    r"pam_\w+\((?P<svc>[^:)]+):auth\): authentication failure;"
    r".*?rhost=(?P<rhost>\S*)\s+user=(?P<user>\S*)")


def _parse_journal_line(msg):
    """(user, how) if a journal message is a refused login, else None."""
    m = PAM_FAIL.search(msg)
    if not m:
        return None
    svc = m.group("svc")
    how = PAM_SERVICES.get(svc, f"via {svc}")
    if m.group("rhost"):
        how += f" from {m.group('rhost')}"
    return m.group("user"), how


def recent_failures_linux(minutes=5):
    """Refused logins from the systemd journal, in the same shape as the
    Windows events. Needs root (or the systemd-journal group) to see them."""
    try:
        r = subprocess.run(
            ["journalctl", "--no-pager", "-q", "-o", "json",
             f"--since=-{minutes}min",
             # auth (4) and authpriv (10) -- where PAM, login and sshd log
             "SYSLOG_FACILITY=4", "SYSLOG_FACILITY=10"],
            capture_output=True, text=True, timeout=25)
    except Exception:
        return []
    events = []
    for line in r.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE")
        if not isinstance(msg, str):          # binary messages arrive as lists
            continue
        hit = _parse_journal_line(msg)
        if not hit:
            continue
        ts = datetime.fromtimestamp(int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6)
        events.append({"record": e.get("__CURSOR"),
                       "time": f"{ts:%Y-%m-%d %H:%M:%S}",
                       "user": hit[0], "how": hit[1]})
    return events


# ------------------------- Windows (Security log) -------------------------

def recent_failures(minutes=5):
    """Failed-login events from the Security log in the last `minutes`.

    Uses PowerShell's Get-WinEvent. Returns a list of dicts, newest first, or
    [] if the log can't be read (e.g. not running as SYSTEM / admin).

    Property indices for a 4625 record: [5] TargetUserName, [9] SubStatus
    (why it failed), [10] LogonType (how it was attempted).
    """
    if not IS_WIN:
        return recent_failures_linux(minutes)
    ids = ",".join(str(i) for i in FAIL_IDS)
    script = (
        f"$start=(Get-Date).AddMinutes(-{minutes}); "
        f"Get-WinEvent -FilterHashtable @{{LogName='Security';Id={ids};"
        f"StartTime=$start}} -ErrorAction SilentlyContinue | "
        "ForEach-Object { [pscustomobject]@{ "
        "id=$_.Id; time=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); "
        "record=$_.RecordId; "
        "user=[string]$_.Properties[5].Value; "
        "substatus=[string]$_.Properties[9].Value; "
        "logontype=[string]$_.Properties[10].Value; } } | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", script],
                           capture_output=True, text=True, timeout=25, **PS_FLAGS)
        out = r.stdout.strip()
        if not out:
            return []
        data = json.loads(out)
        return [data] if isinstance(data, dict) else data
    except Exception:
        return []


def _norm_status(v):
    """Normalise a SubStatus value (int, '12345', or '0x...') to '0x........'."""
    if v is None or v == "":
        return ""
    s = str(v).strip()
    try:
        n = int(s, 16) if s.lower().startswith("0x") else int(s)
        return f"0x{n & 0xffffffff:08x}"
    except ValueError:
        return s.lower()


def _describe(e):
    """One human-readable line for a failed-login event."""
    if "how" in e:                                  # Linux event
        reason, how = "wrong password", e["how"]
    else:
        reason = FAIL_REASONS.get(_norm_status(e.get("substatus")),
                                  "login failed")
        how = LOGON_TYPES.get(str(e.get("logontype") or "").strip(), "")
    who = (e.get("user") or "").strip()
    who = who if who and who != "-" else "the lock screen"
    tail = f" ({how})" if how else ""
    return f"{e.get('time')}  -  {reason} at {who}{tail}"


def _seen():
    try:
        with open(STATE, encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def _remember(records):
    try:
        keep = list(records)[-500:]      # don't let it grow forever
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(keep, f)
    except OSError:
        pass


def report_new(minutes=5, with_photo=True):
    """Report any failed logins we haven't already reported. Returns the count."""
    events = recent_failures(minutes)
    if not events:
        return 0
    seen = _seen()
    fresh = [e for e in events if str(e.get("record")) not in seen]
    if not fresh:
        return 0

    host = os.environ.get("COMPUTERNAME") or platform.node() or "this laptop"
    n = len(fresh)
    lines = [f"WARNING: {n} failed login attempt(s) on {host}"]
    for e in sorted(fresh, key=lambda x: x.get("time", "")):
        lines.append("  " + _describe(e))
    lines.append("")
    lines.append("Someone is trying to get past the login screen.")
    send("\n".join(lines))

    if with_photo:
        cam = try_webcam()
        if cam:
            try:
                send_photo(cam, "Camera at the time of the failed login")
            finally:
                try:
                    os.remove(cam)
                except OSError:
                    pass

    seen |= {str(e.get("record")) for e in fresh}
    _remember(seen)
    return n


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"

    if mode == "test":
        # Prove the Telegram path works, regardless of the event log.
        send(f"Anti-theft sentinel test - {datetime.now():%Y-%m-%d %H:%M:%S}. "
             "If you can read this, alerts work.")
        print("Test message sent.")
        return

    if mode == "watch":
        print("Sentinel watching for failed logins. Ctrl+C to stop.")
        while True:
            try:
                got = report_new(minutes=2)
                if got:
                    print(f"{datetime.now():%H:%M:%S}  reported {got}")
            except Exception as e:
                print("watch error:", e)
            time.sleep(5)

    # default: "once"
    got = report_new(minutes=5)
    print(f"Reported {got} new failed-login event(s).")


if __name__ == "__main__":
    main()
