"""The PERDIX OS file layout, in one place.

System (root-owned, written only by /usr/local/bin/perdix-tune through pkexec):
  /etc/perdix/perdix.conf        game mode, network priority (BBR/QoS) and HUD settings (INI)
  /etc/perdix/drivers.conf       GPU profile chosen by the user (INI)
Logs (/var/log/perdix, created by /etc/tmpfiles.d/perdix.conf):
  game_mode.log                  what game mode changed: services paused, governor, GPU profile
  perf.log                       one line per game session: FPS and frame-time (latency) figures
  mangohud/                      MangoHud's raw per-session logs that perf.log is made from
User (~/.config/perdix-os):
  theme.json                     neon orange / blue accent colours of the desktop and Control Center
  cursor.conf                    cursor theme and size (read by .xinitrc at login)
  settings.json                  Control Center settings (mouse curve, crosshair, zoom, HUD, network,
                                 audio effects, PERDIX Vision)
  vision/luts/                   3D LUTs (.cube) imported in PERDIX Vision
Generated for gamescope (PERDIX Vision): ~/.local/state/perdix/vision/perdix-vision.cube and
~/.local/share/gamescope/reshade/Shaders/perdix_cas_*.fx
  macros.json                    Macro Studio macros
  competitor.json, competitor.env  Competitor Mode state and the game environment it sets
"""

import os

SYSTEM_DIR = "/etc/perdix"
PERDIX_CONF = os.path.join(SYSTEM_DIR, "perdix.conf")
DRIVERS_CONF = os.path.join(SYSTEM_DIR, "drivers.conf")

LOG_DIR = "/var/log/perdix"
GAME_MODE_LOG = os.path.join(LOG_DIR, "game_mode.log")
PERF_LOG = os.path.join(LOG_DIR, "perf.log")
MANGOHUD_LOG_DIR = os.path.join(LOG_DIR, "mangohud")

USER_DIR = os.path.expanduser("~/.config/perdix-os")
THEME = os.path.join(USER_DIR, "theme.json")
CURSOR = os.path.join(USER_DIR, "cursor.conf")
SETTINGS = os.path.join(USER_DIR, "settings.json")
MACROS = os.path.join(USER_DIR, "macros.json")
COMPETITOR_STATE = os.path.join(USER_DIR, "competitor.json")
COMPETITOR_ENV = os.path.join(USER_DIR, "competitor.env")
