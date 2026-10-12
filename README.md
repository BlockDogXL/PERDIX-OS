# PERDIX OS

Arch Linux–based gaming kiosk: silent boot straight into a full-screen PyQt5 shell,
with Steam + Proton and Mesa/Vulkan drivers. See **[PLAN.md](PLAN.md)** for the design
and roadmap, and **[branding/BRANDING.md](branding/BRANDING.md)** for Piri, the mascot,
and where each logo version is used.

Status: the live ISO build script (`tools/build-iso.sh`) is written but not yet tested on an
Arch host. The step scripts in `install/steps/` and most of the shell are still stubs.

```sh
# Try the desktop on a dev machine in a window (needs python-pyqt5; Ctrl+Q quits):
./tools/run-shell-dev.sh

# Open PERDIX Control Center (esports tuning hub) on a dev machine:
cd shell && python -m kiosk_shell.perdix_hub

# PERDIX Studio (code editor + modding tools): see apps/perdix-studio/README.md
python3 apps/perdix-studio/perdix_studio.py

# PERDIX Sentinel / Kılavuz: open-port and file-permission audit (Turkish report)
sudo python3 apps/perdix-kilavuz/perdix_kilavuz.py          # --json, --ports-only, --perms-only
sudo python3 apps/perdix-kilavuz/perdix_kilavuz.py --fix    # hardening plan; --apply applies it
#   (also in Control Center: module 07 Kılavuz, with the 'Açıkları Kapat' button)

# Office and drawing: the desktop's Office (LibreOffice) and Drawing (Krita) tiles; documents
# double-clicked in My Files open in them. Packages: install/packages/office.txt.

# Build the live ISO (on an Arch host, needs archiso >= 86 and ~10 GB free):
sudo pacman -S --needed archiso
sudo ./build_iso.sh                  # standalone, based on Arch's releng profile
sudo ./tools/build-iso.sh            # or: from this repo's archiso/ profile
# Either one takes --nvidia for the proprietary driver.

# On a fresh Arch install (once the steps are implemented):
sudo ./install/install.sh
```
