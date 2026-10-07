# Laptop Monitor Bot + Desktop Pet

Two things for your laptop. They run on Linux (tested on Fedora with GNOME
on Wayland) and on Windows:

1. **Monitor bot** — a Telegram bot that reports system health, online/offline
   status, new files, and screenshots on demand.
2. **Desktop pet** — a small robot that sits in a corner of your screen.

Linux is covered first. The Windows setup is at the end.

---

## What you need first

- **Python 3.10+** (Fedora ships it; check with `python3 --version`).
- A **Telegram account**.
- A few system packages. On Fedora:

  ```
  sudo dnf install python3-tkinter python3-pillow-tk
  ```

  - `python3-tkinter` and `python3-pillow-tk` are needed by the desktop pet.
  - Screenshots under Wayland go through the desktop's screenshot portal,
    which GNOME and KDE already have. It is reached through PyGObject
    (`python3-gobject`, installed with GNOME), which is why the virtual
    environment below is made with `--system-site-packages`. If the portal is
    missing, the bot falls back to `spectacle` (KDE) or `grim` (Sway,
    Hyprland). On an X11 session none of this is needed.
    Recent GNOME blocks `gnome-screenshot` from working in the background,
    so it is only used as a last resort.
  - Optional: `wmctrl` adds window titles to the app report, but only for
    X11/XWayland windows.

  On Debian/Ubuntu the package names are `python3-tk python3-pil.imagetk
  python3-gi`.

---

## Step 1 — Create your Telegram bot

1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, pick a name and a username (must end in `bot`).
3. BotFather replies with a **token** like `123456:ABC-DEF...`. Copy it.

## Step 2 — Install the Python dependencies

In this folder, create a virtual environment and install into it. The
`--system-site-packages` flag lets it see the `python3-tkinter` you installed
and PyGObject you have from `dnf`:

```
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

`run.sh` and the installers pick up `.venv` automatically.

## Step 3 — Add your token

```
cp config.example.py config.py
```

Open `config.py` and paste your token into `BOT_TOKEN`. Save.

## Step 4 — Find your chat id (one time)

1. Run the bot:  `.venv/bin/python monitor_bot.py`
2. In Telegram, open **your** bot and send `/myid`.
3. It replies with a number. Paste that into `CHAT_ID` in `config.py`. Save.
4. Stop the bot (`Ctrl+C`) and start it again.

That id is what lets the bot send you alerts, and it locks the sensitive
commands to you only.

---

## Using it

Send these to your bot in Telegram:

| Command        | What it does                          |
|----------------|---------------------------------------|
| `/status`      | CPU, RAM, disk, battery               |
| `/screenshot`  | photo of the current screen           |
| `/photo`       | webcam photo                          |
| `/disk`        | usage of every drive                  |
| `/net`         | internet status                       |
| `/where`       | Wi-Fi network + approximate location  |
| `/apps`        | what is running, open and closed      |
| `/shutdown`    | shut the laptop down (asks first)     |
| `/abort`       | call off a shutdown                   |
| `/pet`         | current pet settings                  |
| `/setpet`      | then send a picture to hang as the pet |
| `/ropecolor`   | rope colour, e.g. `red`, `#ff8800`    |
| `/rope`        | rope style, `thread` or `chain`       |
| `/petreset`    | back to the default pet               |
| `/help`        | command list                          |

Automatic messages you'll receive:
- **Laptop online** when the bot starts
- **Startup snapshot** - a screenshot and a webcam photo of whoever switched
  the laptop on, sent right after the bot starts. This is an anti-theft
  "who turned my laptop on" snapshot. Turn it off with
  `LOGIN_SNAPSHOT = False` in `config.py`. See the note below.
- **Hourly report** - status, Wi-Fi network and rough location, once an hour.
  Change `STATUS_INTERVAL` in `config.py` (seconds), or set it to `0` to
  switch the report off. The first one arrives `STATUS_FIRST` seconds after
  startup so you can see it works without waiting an hour.
- **App report** - every 5 hours: which apps are open, which are running in
  the background, and what has opened or closed since the last report.
  Change `APP_REPORT_INTERVAL` in `config.py` (seconds), or set it to `0` to
  switch it off. `/apps` gets one on demand.
- **Internet lost / restored**
- **New file: ...** when a file lands in your Downloads folder
  (change the folder in `config.py` -> `WATCH_FOLDER`)
- **Shutdown / Reboot started** - if the laptop is shut down or restarted at
  the machine (see the note below)

### How "open apps" is worked out on Linux

Wayland does not let one program list another program's windows. Instead,
GNOME and KDE start every app you launch in its own systemd scope, and the
bot counts those as open apps. Anything else running under your account is
listed as background. Window titles only appear for X11/XWayland windows,
and only when `wmctrl` is installed.

## Shutting down from Telegram

Send `/shutdown`. The bot replies with how many apps are open and your
battery level, and two buttons: **Confirm shutdown** and **Cancel**.

Nothing happens until you press Confirm. After that the laptop waits before
going down, and `/abort` calls it off in that window. On Linux the wait is
whole minutes (`SHUTDOWN_DELAY` rounded up, so 1 minute by default), because
that is all `shutdown` can schedule. No `sudo` is needed: systemd lets the
user of the active desktop session power off.

The buttons carry their own permission check, so a stranger who somehow
reached your bot cannot press them.

### The shutdown notice, and what it cannot do

If a shutdown or reboot is started **at the laptop**, the bot sends you a
message saying so. On Linux it does this as it is being stopped: it checks
whether systemd is taking the whole machine down, and if so, says which.

**It cannot stop that shutdown, and it is not an anti-theft lock.** Neither
Linux nor Windows lets an ordinary program veto a shutdown, and nothing at
all can help if someone holds the power button or pulls the battery.

So treat it as *"tell me when it happens"*, not *"ask my permission first"*.
If you want a real lock on the machine, that is full-disk encryption (LUKS on
Linux, BitLocker on Windows) plus a firmware password, not a Python script.

### About the location

It is worked out from your **public IP address**, which means it is the point
where your internet provider hands traffic to the internet - not where the
laptop is. On a mobile network it can be out by hundreds of kilometres, and
two lookup services will often name two different cities.

Treat it as *"roughly which city, and has the network changed"*. It is **not**
good enough to find a lost laptop. Set `LOCATION_ENABLED = False` in
`config.py` to leave location out of reports entirely.

The Wi-Fi name and signal strength come from NetworkManager (`nmcli`) on
Linux.

## The desktop pet

Run it any time:

```
.venv/bin/python desktop_pet.py
```

A white inflatable robot sits in the top-right corner. He breathes, blinks,
and waves now and then.

- **Left-click** him - he waves back
- **Right-click** him - closes him

To move him to another corner, either pass it on the command line:

```
.venv/bin/python desktop_pet.py bottom-right
```

or change `CORNER` at the top of `desktop_pet.py`
(`top-right`, `bottom-right`, `top-left`, `bottom-left`). Size, margin, and
the timing of the blinks and waves are constants in the same block.

**On Linux**, Tk has no Wayland support, so the pet runs through XWayland
(already there on any GNOME or KDE Wayland desktop). Tk on Linux also has no
see-through colour, so the window is cut to the robot's outline with the X11
Shape extension instead (`x11_shape.py`). Clicks outside the outline go
through to whatever is underneath.

**Want your own picture instead?** Drop a PNG named `pet.png` next to
`desktop_pet.py` and it is used in place of the drawn robot - transparency is
kept, and it still breathes and floats. Note that a photo or a downloaded
image cannot blink or wave, since those need artwork that can be redrawn in
different poses; and it will look softer than the drawn robot on a
high-resolution screen, because it can only be stretched, not re-rendered.

The original running robot is still here as `desktop_pet_classic.py` if you
prefer it.

### The hanging pet

`hanging_pet.py` hangs the pet from the top edge of the screen on a rope,
like a charm, and swings it with real pendulum physics:

```
.venv/bin/python hanging_pet.py                       # pet.png, else the robot
.venv/bin/python hanging_pet.py other.png --rope chain --at 0.3
```

- **Drag** it sideways and let go - it swings back and forth and slowly
  settles. A gentle breeze sets it swaying now and then.
- **Click** it for a nudge; **right-click** closes it.
- `--rope` is `thread` (gold) or `chain` (silver); `--at` is where along the
  top edge it hangs, from `0` (left) to `1` (right).

Rope length, charm size, swing speed, damping and the breeze are constants
at the top of the file. The rope attaches to the art itself, so a picture
with an empty top-middle (a raised arm, say) still hangs properly.

**From Telegram**, `/setpet` followed by a picture swaps the pet, and
`/ropecolor` and `/rope` change the rope; the running pet redraws itself
within a few seconds. For a picture, a PNG with a transparent background
sent as a *file* works best, since Telegram flattens photos into JPEG. A
normal photo also works when the subject is on a plain background: the bot
cuts the background away and sends back a preview. The choices are saved in
`pet_settings.json` and survive restarts; options given on the command line
take priority over them.

The service runs the hanging pet. For the corner pet instead, change
`hanging_pet.py` to `desktop_pet.py` in `install_autostart.sh` and in
`PET_SCRIPT` in `monitor_bot.py`, then re-run `./install_autostart.sh`.

From Telegram: `/petstop`, `/petstart`, and `/caffeine on|off` (keeps the
laptop from sleeping, idle-locking, or suspending on lid close).

---

## Starting automatically at login (Linux)

```
./install_autostart.sh
```

This installs two systemd user services, `laptop-monitor-bot` and
`laptop-pet`. They start with your desktop session, and systemd restarts them
if they crash (the bot is not restarted if it quits because `config.py` is
wrong). It also makes `config.py` readable only by you, since it holds the
bot token.

Control them with `pet_ctl.sh`, run from this folder:

```
./pet_ctl.sh status            # both
./pet_ctl.sh stop pet          # pet only
./pet_ctl.sh restart bot       # bot only, e.g. after editing config.py
./pet_ctl.sh start
./pet_ctl.sh logs              # follow the logs (journald rotates them)
```

To remove the services: `./install_autostart.sh --remove`.

### The webcam / photo feature - please read

The startup snapshot and `/photo` take a picture with the built-in camera and
send it to your Telegram. On **your own** laptop, as an anti-theft "who is
using my machine" tool, that is a normal thing to do.

Two things to keep in mind:

- **If other people use this laptop** (family, flatmates, a shared machine),
  photographing them silently is a different matter. Tell them it is running,
  or leave `LOGIN_SNAPSHOT = False`. In some places recording someone without
  their knowledge is against the law - the rules vary by country and state.
- The photo needs the camera to be free. If another app (a call, the Camera
  app) is using it, the shot fails and the bot tells you so rather than
  hanging.

Set `LOGIN_SNAPSHOT = False` to stop the automatic startup photo; `/photo`
still works on demand. `CAMERA_INDEX` picks the camera (0 is the built-in
one, `/dev/video0` on Linux).

---

## Anti-theft: alert me when a login fails (Linux)

This is the part that has to be awake *before* anyone logs in, so it can catch
a thief typing the wrong password at the login or lock screen.

**How it is built, and why in two pieces.** Before login there is no desktop
session, so nothing can take a screenshot. What *can* run is a system service
that reads the journal and sends Telegram messages. So the anti-theft alert is
a separate, lightweight script (`antitheft_sentinel.py`) that:

- runs as root from a systemd service started at boot, so it is armed at the
  login screen with nobody logged in,
- watches the journal for PAM's "authentication failure" lines. These cover
  the login and lock screens (`gdm-password`), text consoles, `sudo`, `su`,
  SSH and admin password prompts, and the alert says which one it was,
- only *sends* messages, so it never clashes with the main bot (Telegram
  allows one command-poller per bot, but any number of senders).

The interactive bot (screenshots, webcam, `/status`, and the rest) still
starts at login, because before login there is nothing for it to do.

### Install

`config.py` must already hold your token and chat id. Then:

```
sudo ./install_antitheft.sh
```

It registers and starts `laptop-monitor-sentinel.service`, sends a test
message, and prints any refused logins already in the journal from the last
24 hours. Logs: `journalctl -u laptop-monitor-sentinel`.

### Test it

Lock the screen (`Super + L`), type a **wrong** password once, then log in
properly. Within a few seconds you should get a Telegram alert naming the
time, the account, and where it was typed. A webcam photo is sent too if the
camera is free.

### To remove it

```
sudo ./uninstall_antitheft.sh
```

### Honest limits

- A wrong **fingerprint** is often not logged as an authentication failure,
  so it may not trigger an alert. A wrong password or PIN always is.
- It reports failures; it cannot *stop* anyone. Someone who never tries to log
  in (boots a USB stick, or pulls the drive) leaves no failed-login trace. For
  the machine to actually resist theft you want full-disk encryption plus a
  firmware/BIOS password - a script cannot do that job.

## Notes & limits

- The bot only works while the laptop is **on** and the script is **running**.
  Nothing can report from a powered-off machine.
- A clean shutdown/restart just stops the bot; you'll get the "Laptop online"
  message again next time it starts.
- Keep your bot token private — anyone with it can control the bot.

---

## Windows

Everything above works on Windows too, with these differences:

- Install Python from python.org (tick **"Add Python to PATH"**), then
  `pip install -r requirements.txt`. No extra packages are needed.
- **Auto-start:** put a shortcut to `run.bat` in the folder that
  `Win + R` → `shell:startup` opens. The bot's console window starts
  minimised; it needs a real console to hear Windows' shutdown event.
- **Shutdown delay** is `SHUTDOWN_DELAY` seconds exactly (30 by default).
- **Open apps** are the processes with a visible window, with their titles.
- **Location:** Windows reports Wi-Fi signal strength only with Location
  services on (`Win + R` → `ms-settings:privacy-location`).
- **Anti-theft:** the sentinel runs as a SYSTEM scheduled task triggered by
  failed-logon events 4625/4776 in the Security log, and can tell a wrong PIN
  from a wrong password. Install and remove it from an elevated PowerShell:

  ```
  Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy','Bypass','-File','D:\Mointor\install_antitheft.ps1'
  Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy','Bypass','-File','D:\Mointor\uninstall_antitheft.ps1'
  ```

  The installer has the folder `D:\Mointor` and `C:\Python314\pythonw.exe`
  written into it; edit `$Dir` and `$Pyw` at the top if yours differ.
  Windows usually blocks the camera at the lock screen, so most often you get
  the text alert alone.
