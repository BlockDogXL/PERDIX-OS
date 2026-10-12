# PERDIX OS

Arch Linux–based gaming kiosk: silent boot straight into a full-screen PyQt5 shell,
with Steam + Proton and Mesa/Vulkan drivers. See **[PLAN.md](PLAN.md)** for the design
and roadmap.

Status: the live ISO build script (`tools/build-iso.sh`) is written but not yet tested on an
Arch host. The step scripts in `install/steps/` and most of the shell are still stubs.

```sh
# Try the desktop on a dev machine in a window (needs python-pyqt5; Ctrl+Q quits):
./tools/run-shell-dev.sh

# Build the live ISO (on an Arch host, needs archiso >= 86 and ~10 GB free):
sudo pacman -S --needed archiso
sudo ./build_iso.sh                  # standalone, based on Arch's releng profile
sudo ./tools/build-iso.sh            # or: from this repo's archiso/ profile
# Either one takes --nvidia for the proprietary driver.

# On a fresh Arch install (once the steps are implemented):
sudo ./install/install.sh
```
