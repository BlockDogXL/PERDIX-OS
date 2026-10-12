# PERDIX OS — Build Plan

A gaming-focused kiosk operating system: **Arch Linux** underneath, a **silent boot**
straight into a **full-screen Python desktop shell**, with **Steam + Proton** and the
Mesa/Vulkan stack for gaming. The user never sees a login prompt, boot logs or a
general-purpose desktop.

---

## 1. Goals and non-goals

**Goals**

- Power on → splash screen → full-screen shell, with no text output visible.
- Launch Steam (Big Picture / gamepad UI) and Proton games from the shell.
- Working GPU acceleration on AMD, Intel and NVIDIA (Vulkan + 32-bit libs).
- Shell controllable by mouse, keyboard **and** gamepad.
- The shell restarts on its own if it crashes; the user can't get to a terminal.
- Reproducible install: one script on a fresh Arch install, and later a bootable ISO.

**Non-goals (for now)**

- Multi-user support, a general app store, Secure Boot signing, A/B atomic updates.

---

## 2. Key technical decisions

| Topic | Choice | Why |
|---|---|---|
| GUI toolkit | **PyQt5** (not CustomTkinter) | Hardware-accelerated rendering, proper full-screen/frameless handling, QML option for console-style UIs, mature on Linux. Tkinter has no GPU acceleration and handles focus badly when a game window takes over. |
| Display server | **Xorg** + minimal WM (**Openbox**) started via `startx` | Steam's client and many Proton titles are most reliable on X11. A tiny WM handles focus/fullscreen hand-off between the shell and games. Wayland / gamescope is an optional later phase (see §9). |
| Session start | `getty@tty1` **autologin** → `~/.bash_profile` → `startx` | Simple, well documented, no display manager to theme or hide. |
| Silent boot | Kernel `quiet` flags + **Plymouth** splash + hidden bootloader menu | Hides kernel/systemd text from power-on to the shell. |
| Bootloader | **systemd-boot** (UEFI) | Minimal; `timeout 0` hides the menu (hold Space to show it for maintenance). |
| Kernel | `linux-zen` (fallback: `linux`) | Desktop/gaming tuned scheduler; stock kernel kept as a fallback entry. |
| Audio | PipeWire (+ `pipewire-pulse`, `lib32-pipewire`), WirePlumber, EasyEffects | What Steam/Proton expect today; EasyEffects for one-click effects. |
| Network | NetworkManager + `nmcli` (Control Center module 06 drives it) | Wi-Fi setup from inside the shell; Ethernet comes up on its own (DHCP). |
| Gamepad in shell | `python-evdev` | Reads controllers directly; maps to Qt key events. |
| Proton | Steam's built-in Proton; optional **Proton-GE** | Steam Play handles Windows titles; GE for extra compatibility. |

---

## 3. Boot flow (target)

```
UEFI
 └─ systemd-boot (menu hidden, timeout 0)
     └─ linux-zen + initramfs (plymouth hook)   ← "quiet splash loglevel=3 ..."
         └─ Plymouth splash (custom theme)
             └─ systemd → multi-user.target
                 └─ getty@tty1 (autologin user "kiosk", no issue/motd)
                     └─ ~/.bash_profile → exec startx (output → /dev/null)
                         └─ ~/.xinitrc
                             ├─ openbox (no decorations, no menus)
                             └─ kiosk-session (loop: restart shell if it exits)
                                 └─ python -m kiosk_shell  (full-screen PyQt5)
                                     ├─ Launch Steam  → steam -gamepadui
                                     ├─ Settings (Wi-Fi, audio, display)
                                     └─ Power (shutdown / reboot via polkit)
```

---

## 4. Step-by-step process

### Phase 0 — Development environment
1. Create an Arch VM (QEMU/virt-manager, UEFI firmware, VirtIO GPU with 3D
   acceleration) for fast iteration; keep a spare physical machine for GPU tests.
2. Develop the shell on a normal desktop first: `tools/run-shell-dev.sh` runs it in
   a window (`KIOSK_DEV=1`) without needing the full OS.

### Phase 1 — Base system
1. Install a minimal Arch with `archinstall` (or by hand): UEFI, systemd-boot, ext4/btrfs,
   `linux-zen` + `linux`, NetworkManager.
2. Clone this repo onto the machine and run `sudo ./install/install.sh`.
3. `steps/10-base.sh` — enable `[multilib]` in `/etc/pacman.conf`, full system
   upgrade, install `packages/base.txt` (Xorg, Openbox, PipeWire, fonts, NetworkManager).

### Phase 2 — Graphics and gaming stack
1. `steps/20-gpu-drivers.sh` — detect the GPU vendor with `lspci` and install the
   matching list:
   - AMD: `mesa lib32-mesa vulkan-radeon lib32-vulkan-radeon`
   - Intel: `mesa lib32-mesa vulkan-intel lib32-vulkan-intel intel-media-driver`
   - NVIDIA: `nvidia-open-dkms nvidia-utils lib32-nvidia-utils` + `nvidia_drm.modeset=1`
   - Always: `vulkan-icd-loader lib32-vulkan-icd-loader vulkan-tools`
2. `steps/30-steam.sh` — install `steam`, `gamemode lib32-gamemode`, `mangohud
   lib32-mangohud`, `ttf-liberation`; apply `vm.max_map_count` sysctl
   (needed by many Proton titles).
3. Verify: `vulkaninfo --summary`, then launch Steam once, enable
   *Steam Play for all titles*, run a Proton game.

### Phase 3 — Kiosk user and autologin
1. `steps/40-kiosk-user.sh` — create user `kiosk` (groups: `video,audio,input`),
   **no sudo**, locked password; install `skel/` files into its home.
2. `steps/50-autologin.sh` — install the `getty@tty1` drop-in from `rootfs/`
   (autologin, `--noissue`, `--nohints`); `touch ~kiosk/.hushlogin`.
3. `~/.bash_profile` starts X only on tty1: `exec startx -- -keeptty >/dev/null 2>&1`.

### Phase 4 — Silent boot
1. `steps/60-silent-boot.sh`:
   - Kernel cmdline: `quiet splash loglevel=3 rd.udev.log_level=3
     systemd.show_status=auto vt.global_cursor_default=0`.
   - Install `packages/boot.txt` (`plymouth`) and the **Piri theme** (`rootfs/usr/share/plymouth/themes/perdix`,
     see `branding/BRANDING.md`), add the `plymouth` hook to `mkinitcpio.conf`, set the
     custom theme, regenerate the initramfs.
   - systemd-boot `loader.conf`: `timeout 0`, `console-mode keep`.
   - Leave `getty@tty2..6` alone: VT switching is disabled in Xorg instead (§Phase 6),
     and recovery goes through the hidden boot menu (§7).
   - `kernel.printk = 3 3 3 3` so late kernel messages don't hit the screen.

### Phase 5 — The desktop shell (PyQt5)
1. `steps/70-shell.sh` — install `python-pyqt5 python-evdev python-dbus`, copy
   `shell/` to `/opt/kiosk-shell`, install `kiosk-session` to `/usr/local/bin`.
2. Shell features, in build order (`[x]` = in `kiosk_shell/main_desktop.py`):
   1. [x] Full-screen frameless window that ignores close requests (Alt+F4, WM close,
      Ctrl+C) and re-enters full screen if minimised. Black + neon-blue theme, animated logo.
   2. [x] Home screen tiles: **Open Games (Steam)**, **My Files** (browser locked to the home
      folder; double-clicking a document opens it in LibreOffice or Krita), **Office**
      (LibreOffice: opens and saves .docx/.xlsx/.pptx and OpenDocument in their own format),
      **Çizim Stüdyosu** (Krita, with its MyPaint brush engine), **Antivirus Scan** (ClamAV `clamscan` on the home folder;
      `clamav-freshclam.service` keeps definitions current), **Shut Down System**
      (confirm dialog with Shut Down / Restart / Cancel).
   3. [x] Launcher service: start Steam (`KIOSK_STEAM_CMD`, default `steam -gamepadui`); the
      shell stays underneath, pauses its animation, and comes back to the front on exit.
   4. Gamepad navigation (`input/gamepad.py`, evdev → Qt key events).
   5. Settings: [x] Wi-Fi and Ethernet (Control Center → Network Settings, `nmcli`; polkit lets the
      kiosk user join networks), [x] sound (Control Center → Audio: PipeWire devices and volume
      through `wpctl`, one-click EasyEffects volume boost / bass boost / mic noise suppression),
      resolution (`xrandr`).
   5b. [x] PERDIX Vision (Control Center → Vision): TacticalVision-style picture tuning for games.
      Steam runs inside gamescope; colour (brightness, contrast, vibrance, lift/gamma/gain and an
      imported .cube LUT) is baked into one 3D LUT set through GAMESCOPE_COLOR_LOOK_G22, sharpening
      is a CAS ReShade effect (GAMESCOPE_RESHADE_EFFECT); both change live.
   6. [x] Power: `systemctl poweroff/reboot` (allowed by the polkit rule).
   6b. [x] Branding: Piri (neon blue) in the centre, Piri icon top-left opening an
       **About System** page (version from `/etc/perdix-release`, kernel, architecture,
       CPU, GPU, memory).
   7. Optional: list installed Steam games by parsing `steamapps/*.acf` and launch
      them directly with `steam steam://rungameid/<appid>`.
3. **PERDIX Control Center** (`shell/kiosk_shell/perdix_hub.py`): frameless esports hub, graphite
   with neon-orange / ice-blue accents. Modules:
   - 01 Sens & Accel: Raw Accel-style curves (Off / Linear / Natural / Classic) sent to libinput's
     custom acceleration profile through xinput as you change them, with a live graph; saved to
     `~/.config/perdix-os/settings.json` and re-applied at login (`perdix_hub --apply-saved` in `.xinitrc`).
     eDPI / cm-per-360 calculator and game-to-game converter.
   - 02 Tactical Scope: crosshair designer + overlay (X11 shape mask, click-through); zoom key
     watched with evdev, zooming the screen centre with `xrandr --transform` (Hyprland: `hyprctl`).
   - 03 Macro Studio: key / text / delay macros, JSON, played on a uinput virtual keyboard.
   - 04 Game Mode & HUD: ULTRA FPS / COMPETITOR MODE (`gamemoded -r`, CPU governor `performance`,
     vsync-off env for Steam) and the PERDIX HUD theme engine editing `MangoHud.conf` in place.
   - 05 Network (Zero-Ping): BBR + a `tc` policy (game UDP ports first, TCP downloads policed).
   Root changes go through `pkexec /usr/local/bin/perdix-tune`, a validated helper the kiosk
   user may run without a password (polkit action `org.perdix.tune`).
4. `kiosk-session` wraps the shell in a restart loop with crash back-off and logs
   to `~/.local/state/kiosk/shell.log`.

### Phase 6 — Lockdown
1. `steps/80-lockdown.sh`:
   - Xorg `ServerFlags`: `DontVTSwitch`, `DontZap` (no Ctrl+Alt+F*/Backspace escape).
   - Openbox `rc.xml` with no keybindings except what the shell needs, no root menu.
   - polkit rule: user `kiosk` may only power off / reboot / suspend.
   - Disable SysRq (`kernel.sysrq = 0`); disable the systemd emergency shell prompt
     only if a maintenance path exists (hidden boot entry with `systemd.unit=multi-user.target`
     and no autologin — see §7).

### Phase 7 — Packaging (ISO)
1. **Live ISO (script written, not yet test-built):** `sudo tools/build-iso.sh [--nvidia] [--root-password]` on an Arch host
   with `archiso` ≥ 86. It stages `archiso/` + `rootfs/` + `skel/` + `shell/` into
   `work/profile`, merges `archiso/packages.live.txt` with `install/packages/{base,boot,gaming,
   gpu-amd,gpu-intel,shell}.txt`, and runs `mkarchiso`. Output: `out/perdix-os-<date>-x86_64.iso`.
   - Boots BIOS (syslinux) and UEFI (systemd-boot) with no menu, quiet cmdline and Plymouth.
   - User `kiosk` (UID 1000, locked password) is created by `etc/sysusers.d/kiosk.conf` and
     autologged in on tty1 → startx → shell. Root is locked unless `--root-password`.
   - Maintenance: hold Space (UEFI) or Shift then type `maintenance` (BIOS) → console, no X.
   - No desktop environment: only Xorg + Openbox + the PyQt5 shell.
   - Live limits: everything written (Steam client, games) lives in a 4 GB RAM overlay and is
     lost on reboot. Proton is not a pacman package; Steam downloads it on first use.
   - `build_iso.sh` (repo root) is a standalone alternative: it starts from Arch's releng
     profile, writes its config inline and only copies `shell/kiosk_shell` from the repo.
2. **Installer ISO (todo):** add an unattended installer that partitions a disk and runs
   `install/install.sh` in a chroot, so Steam and games persist.
3. Test the ISO in the VM, then on real AMD/Intel/NVIDIA hardware.

### Phase 8 — System tools (sub-plan; built one item at a time, one commit each)

The Control Center grows from 9 to 20 modules, so first (step 0) its menu gets group headings
(Gaming · System · Media · Maintenance) and scrolls. Every item below is a module of
`perdix_hub.py` with its logic in `kiosk_shell/services/`, tests in `shell/tests/`, and anything
that needs root goes through `perdix-tune` (validated arguments, pkexec, polkit) or a narrow
polkit rule for a system service. Status: `[ ]` todo, `[x]` done.

| # | Module | How | Packages | Root / limits | Verified by |
|---|---|---|---|---|---|
| 1 | [x] Task Manager | `psutil` for CPU, RAM, per-process use; GPU from amdgpu sysfs (`gpu_busy_percent`, VRAM) or `nvidia-smi`; "Not responding" = the window ignores the EWMH `_NET_WM_PING` (what Windows calls a hung window); End task = SIGTERM, then SIGKILL | python-psutil | own processes only; the desktop shell and system processes are protected | psutil on this machine; ping on a real X server with a responsive and a frozen window |
| 2 | [x] Devices | `lspci -k` (driver in use, modules), `lsusb`, `/proc/bus/input/devices` (keyboards, mice), DMI for the motherboard | pciutils, usbutils | none | parsers against real command output |
| 3 | [x] System Diagnostics | GPU, driver, Vulkan (`vulkaninfo --summary`), OpenGL (`glxinfo -B`), DirectX = what DXVK / VKD3D-Proton can offer on that Vulkan version (Linux has no native DirectX), RAM, BIOS (DMI), exportable report | vulkan-tools, mesa-utils | none | real tool output where available |
| 4 | [ ] Region & Language | `timedatectl` (zone, NTP), `localectl` (X11 keyboard layout, system language) + `setxkbmap` for the running session | (systemd) | polkit rule for timedate1/locale1 actions; only generated locales | parsers; commands recorded |
| 5 | [ ] Bluetooth | `bluetoothctl` (scan, pair, trust, connect, remove); Blueman as the full tool | bluez, bluez-utils, blueman | kiosk user allowed by BlueZ's D-Bus policy / polkit | parser against real bluetoothctl output; no radio here |
| 6 | [ ] Screen & audio sharing | PERDIX runs Xorg, where Discord/OBS/browsers capture the screen directly; the module checks the pieces (PipeWire, portals) and adds a "stream mix": a PipeWire virtual microphone carrying game audio + mic for Discord | xdg-desktop-portal, -gtk | no root | real PipeWire with virtual devices |
| 7 | [ ] Camera & microphone test | `v4l2-ctl` (devices, formats/resolutions, controls) + live preview; microphone level meter from PipeWire and gain via `wpctl` | v4l-utils | none | parsers; PipeWire with a virtual mic |
| 8 | [ ] Game recording | OBS Studio (libobs) running in the background, driven over obs-websocket 5; hardware encoder (VAAPI / NVENC) so the game keeps its FPS; F9 through the existing evdev key watcher | obs-studio | input group already granted | websocket protocol against a test server; real FPS cost needs hardware |
| 9 | [ ] Lossless video cutter | the LosslessCut approach: `ffmpeg -c copy` cut and concat (no re-encode), with a preview player | ffmpeg | none | real ffmpeg on generated clips |
| 10 | [ ] Smart updater | `checkupdates` (safe: separate database copy), update through `perdix-tune update` after a snapshot (item 11), neon-orange notification on the desktop | pacman-contrib | perdix-tune | commands recorded; live ISO cannot keep updates |
| 11 | [ ] Recovery center | Timeshift snapshots: create, list, restore, delete; automatic snapshot before updates | timeshift | perdix-tune | commands recorded; restore needs an installed system |

Network access to archlinux.org is blocked in the development environment, so package names
that could not be checked against the Arch repositories are listed in each item's commit.

---

## 5. Scripts to write

| Script | Purpose |
|---|---|
| `install/install.sh` | Orchestrator: root check, run `steps/*.sh` in order, `--only <step>` / `--skip <step>`, logs to `/var/log/kiosk-install.log`. |
| `install/lib/common.sh` | Shared helpers: logging, `pacman_install_list`, `copy_rootfs`, idempotency guards. |
| `install/steps/10-base.sh` | multilib, system upgrade, base packages. |
| `install/steps/20-gpu-drivers.sh` | GPU detection + driver packages, NVIDIA modeset. |
| `install/steps/30-steam.sh` | Steam, gamemode, MangoHud, sysctl tuning. |
| `install/steps/40-kiosk-user.sh` | Create `kiosk` user, install skel files. |
| `install/steps/50-autologin.sh` | getty autologin drop-in, hushlogin. |
| `install/steps/60-silent-boot.sh` | Kernel cmdline, Plymouth, loader.conf, printk. |
| `install/steps/70-shell.sh` | Python deps, deploy shell to `/opt/kiosk-shell`. |
| `install/steps/80-lockdown.sh` | Xorg flags, Openbox config, polkit, sysctl. |
| `rootfs/usr/local/bin/kiosk-session` | Restart loop around the shell. |
| `tools/run-shell-dev.sh` | Run the shell windowed on a dev machine. |
| `tools/build-iso.sh` | Assemble the archiso profile and build the live ISO (see Phase 7). |

All step scripts must be **idempotent** (safe to run twice) and use `set -euo pipefail`.

---

## 6. Folder structure

```
perdix-os/
├── PLAN.md                     ← this file
├── README.md
├── build_iso.sh                ← standalone ISO build from Arch's releng profile
├── install/
│   ├── install.sh              ← entry point (run as root)
│   ├── lib/common.sh
│   ├── packages/               ← one package per line, '#' comments allowed
│   │   ├── admin.txt           ← installed system only (base-devel, git)
│   │   ├── base.txt
│   │   ├── boot.txt
│   │   ├── gaming.txt
│   │   ├── gpu-amd.txt
│   │   ├── gpu-intel.txt
│   │   ├── gpu-nvidia.txt
│   │   └── shell.txt
│   └── steps/                  ← executed in numeric order
│       ├── 10-base.sh
│       ├── 20-gpu-drivers.sh
│       ├── 30-steam.sh
│       ├── 40-kiosk-user.sh
│       ├── 50-autologin.sh
│       ├── 60-silent-boot.sh
│       ├── 70-shell.sh
│       └── 80-lockdown.sh
├── rootfs/                     ← files copied verbatim onto the target "/"
│   ├── etc/
│   │   ├── kiosk/kiosk.conf
│   │   ├── polkit-1/rules.d/50-kiosk-power.rules
│   │   ├── sysctl.d/80-kiosk.conf
│   │   ├── systemd/system/getty@tty1.service.d/autologin.conf
│   │   └── X11/xorg.conf.d/10-kiosk.conf
│   └── usr/local/bin/kiosk-session
├── skel/                       ← dotfiles for /home/kiosk
│   ├── .bash_profile
│   └── .xinitrc
├── shell/                      ← the PyQt5 desktop shell
│   ├── pyproject.toml
│   ├── kiosk_shell/
│   │   ├── __main__.py
│   │   ├── main_desktop.py     ← the desktop UI (home, files, scan, shutdown dialog)
│   │   ├── config.py           ← reads /etc/kiosk/kiosk.conf
│   │   ├── perdix_hub.py       ← PERDIX Control Center (esports tuning hub)
│   │   ├── services/           ← launcher, power, antivirus, system info; hub: mouse, macros,
│   │   │                          hud, competitor, tuning, magnifier, keywatch, hub_settings
│   │   ├── input/              ← gamepad handling
│   │   └── resources/          ← icons, themes (QSS)
│   └── tests/
├── branding/                   ← Piri logo master + generated variants, BRANDING.md
├── archiso/                    ← ISO profile (Phase 7), staged by tools/build-iso.sh
│   ├── profiledef.sh           ← ISO name, boot modes, file ownership (kiosk = UID 1000)
│   ├── pacman.conf             ← build-time repos, incl. [multilib]
│   ├── packages.live.txt       ← kernel, firmware, live-boot hooks, NVK
│   ├── packages.nvidia.txt     ← proprietary NVIDIA driver (--nvidia)
│   ├── efiboot/loader/         ← systemd-boot: hidden menu, kiosk + maintenance entries
│   ├── syslinux/syslinux.cfg   ← BIOS boot, same two entries
│   └── airootfs/etc/           ← ISO-only files: sysusers kiosk user, mkinitcpio hooks, ...
├── tools/                      ← dev helpers
└── docs/                       ← hardware notes, troubleshooting
```

---

## 6b. Runtime flow and file layout

**From power-on to a game**

1. `build_iso.sh` (or `tools/build-iso.sh`) builds an Arch ISO with Python/PyQt5, Mesa/Vulkan
   (and NVIDIA with `--nvidia`), Steam and the PERDIX files. The boot menu and kernel text are
   hidden; Plymouth shows the Piri splash instead.
2. tty1 logs the `kiosk` user in; `.bash_profile` starts X; `.xinitrc` sets the cursor, re-applies
   the mouse curve, starts Openbox and runs `main_desktop.py` full screen, restarting it if it exits.
3. **Open Games (Steam)** starts `steam -gamepadui` (Steam's console-style Big Picture UI), with
   Competitor Mode's environment added while that mode is on. When Steam exits, the session's
   MangoHud log is summarised into `perf.log`.
4. **Control Center** starts `perdix_hub.py` as its own process. Everything it changes as root
   (CPU governor, BBR, `tc` QoS, GPU profile, paused services) goes through
   `pkexec /usr/local/bin/perdix-tune`, which validates every argument and records the result.

**Files** (all paths in `shell/kiosk_shell/paths.py`)

| Path | Written by | Contents |
|---|---|---|
| `/etc/perdix/perdix.conf` | perdix-tune (root) | INI: `[game_mode]` competitor, governor · `[network]` tcp, qos, download_limit_mbit, game_ports · `[hud]` color, position |
| `/etc/perdix/drivers.conf` | perdix-tune (root) | INI: `[gpu]` profile (auto / performance / powersave), cards |
| `/var/log/perdix/game_mode.log` | perdix-tune (root) | Competitor Mode on/off, governor, GPU profile, which background services were stopped and restarted |
| `/var/log/perdix/perf.log` | desktop / Control Center (group `perdix`) | One line per game session: avg FPS, 1 % low FPS, average and 99th-percentile frame time |
| `/var/log/perdix/mangohud/` | MangoHud (group `perdix`) | Raw session logs perf.log is made from; kept 7 days after summarising |
| `~/.config/perdix-os/theme.json` | user | Accent colours: orange, ice, blue (desktop neon) |
| `~/.config/perdix-os/cursor.conf` | user / make-cursor-theme.py | `XCURSOR_THEME`, `XCURSOR_SIZE`, read by `.xinitrc` |
| `~/.config/perdix-os/settings.json` | Control Center | Mouse curve, crosshair, zoom key, HUD layout, network form |
| `~/.config/perdix-os/macros.json` | Macro Studio | Macros |
| `~/.config/perdix-os/competitor.{json,env}` | Competitor Mode | State (ends with the boot) and the game environment for Steam |

`/etc/tmpfiles.d/perdix.conf` creates the system folders, `/etc/sysusers.d/perdix.conf` the
`perdix` group, and `perdix-restore.service` re-applies `perdix.conf` / `drivers.conf` at boot
(BBR, QoS, GPU profile; game mode always starts off).

---

## 7. Maintenance / recovery path

A kiosk with no escape hatch is a brick when something breaks. Plan:

- Hidden systemd-boot entry **"Maintenance"**: same kernel, cmdline adds
  `systemd.unit=multi-user.target kiosk.maintenance=1`; `.bash_profile` skips `startx`
  when that flag is in `/proc/cmdline`, and autologin is skipped so an admin logs in
  on the console.
- Reach it by holding **Space** during power-on (systemd-boot shows the menu).
- An `admin` account with sudo exists only for this path (password set at install).

---

## 8. Testing checklist

- [ ] Cold boot shows no text between firmware logo and shell.
- [ ] Ctrl+Alt+F2, Ctrl+Alt+Backspace, Alt+F4 on the shell do nothing harmful.
- [ ] Killing the shell (`pkill -f kiosk_shell`) → it comes back within a few seconds.
- [ ] `vulkaninfo --summary` lists the real GPU (not llvmpipe).
- [ ] Steam launches in gamepad UI, returns to the shell on exit.
- [ ] A Proton title (e.g. a free Windows-only game) runs.
- [ ] Gamepad can navigate every shell screen.
- [ ] Wi-Fi can be joined from Control Center → Network Settings (needs real Wi-Fi hardware).
- [ ] A network cable works with no action, and again after unplugging and replugging.
- [ ] Power off / reboot from the shell works without a password.
- [ ] Maintenance boot entry gives a console login.

---

## 9. Later / optional

- **gamescope session**: run Steam inside `gamescope` (SteamOS-style) for better frame
  pacing, HDR and VRR; potentially move the whole session to Wayland.
- Read-only root / btrfs snapshots before updates (`snapper` + rollback entry).
- Update flow inside the shell (`pacman -Syu` via a privileged helper service).
- Custom Plymouth theme matching the shell's look.
- Proton-GE management from Settings.
