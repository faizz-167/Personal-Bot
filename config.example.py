import os

# ==================== Monitor Bot settings ====================

# 1) In Telegram, open @BotFather -> /newbot -> follow prompts.
#    It gives you a token like 123456:ABC-DEF...  Paste it here:
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"

# 2) Your personal chat id. Leave "" for now.
#    Run the bot, send it /myid, paste the number here, then restart.
#    Alerts (online/offline, new files) need this to know where to send.
CHAT_ID = ""

# 3) Folder to watch for new files (default: your Downloads folder)
WATCH_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")

# 4) How often (seconds) to check the internet connection
NET_CHECK_INTERVAL = 60

# 5) How often (seconds) to push an unprompted status report.
#    3600 = once an hour. Set to 0 to turn the hourly report off.
STATUS_INTERVAL = 3600

# 6) Seconds after startup before the first report is sent, so you can see
#    straight away that it works. Raise it if you find that noisy.
STATUS_FIRST = 30

# 7) How often (seconds) to send the detailed running-apps report.
#    18000 = every 5 hours. Set to 0 to turn it off.
APP_REPORT_INTERVAL = 18000

# 8) Seconds to wait before actually shutting down, once you approve
#    a /shutdown from Telegram. Gives you time to send /abort.
#    Linux can only schedule whole minutes, so this is rounded up there.
SHUTDOWN_DELAY = 30

# 9) When the bot starts (i.e. when you switch the laptop on), send a
#    screenshot and a webcam photo of whoever is at the machine.
#    This is an anti-theft "who turned my laptop on" snapshot.
LOGIN_SNAPSHOT = True

# 10) Which camera to use (0 is the built-in one) and how many frames to
#     throw away first so the exposure has time to settle.
CAMERA_INDEX = 0
CAMERA_WARMUP = 8

# 11) Include an approximate location in reports.
#    This asks ipinfo.io where your public IP is, so that service sees your
#    IP address. It is city-level at best -- see the note in the README.
LOCATION_ENABLED = True
