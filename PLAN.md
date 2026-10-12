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
| Audio | PipeWire (+ `pipewire-pulse`, `lib32-pipewire`) | What Steam/Proton expect today. |
| Network | NetworkManager (shell drives it via `nmcli`/D-Bus) | Wi-Fi setup from inside the shell. |
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
   - Install `plymouth`, add the `plymouth` hook to `mkinitcpio.conf`, set the
     custom theme, regenerate the initramfs.
   - systemd-boot `loader.conf`: `timeout 0`, `console-mode keep`.
   - Leave `getty@tty2..6` alone: VT switching is disabled in Xorg instead (§Phase 6),
     and recovery goes through the hidden boot menu (§7).
   - `kernel.printk = 3 3 3 3` so late kernel messages don't hit the screen.

### Phase 5 — The desktop shell (PyQt5)
1. `steps/70-shell.sh` — install `python-pyqt5 python-evdev python-dbus`, copy
   `shell/` to `/opt/kiosk-shell`, install `kiosk-session` to `/usr/local/bin`.
2. Shell features, in build order:
   1. Full-screen frameless window, cursor hidden on gamepad use, theme loader.
   2. Home screen: big tiles — **Steam**, **Settings**, **Power**.
   3. Launcher service: start Steam (`steam -gamepadui`), hide/minimise the shell while
      it runs, restore when the process exits.
   4. Gamepad navigation (`input/gamepad.py`, evdev → Qt key events).
   5. Settings: Wi-Fi (NetworkManager D-Bus), volume (`wpctl`), resolution (`xrandr`).
   6. Power menu: `systemctl poweroff/reboot` (allowed by the polkit rule).
   7. Optional: list installed Steam games by parsing `steamapps/*.acf` and launch
      them directly with `steam steam://rungameid/<appid>`.
3. `kiosk-session` wraps the shell in a restart loop with crash back-off and logs
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
1. `archiso/` — copy the `releng` profile, add our package lists, `rootfs/` as
   `airootfs/`, and an unattended installer.
2. Build with `mkarchiso -v -w /tmp/work -o out/ archiso/`.
3. Test the ISO in the VM, then on real AMD/Intel/NVIDIA hardware.

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
| `tools/build-iso.sh` | Wrapper around `mkarchiso`. |

All step scripts must be **idempotent** (safe to run twice) and use `set -euo pipefail`.

---

## 6. Folder structure

```
perdix-os/
├── PLAN.md                     ← this file
├── README.md
├── install/
│   ├── install.sh              ← entry point (run as root)
│   ├── lib/common.sh
│   ├── packages/               ← one package per line, '#' comments allowed
│   │   ├── base.txt
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
│   │   ├── app.py
│   │   ├── ui/                 ← screens and widgets
│   │   ├── services/           ← launcher, power, network, audio
│   │   ├── input/              ← gamepad handling
│   │   └── resources/          ← icons, themes (QSS)
│   └── tests/
├── archiso/                    ← ISO profile (Phase 7)
├── tools/                      ← dev helpers
└── docs/                       ← hardware notes, troubleshooting
```

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
- [ ] Wi-Fi can be joined from Settings.
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
