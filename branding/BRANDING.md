# PERDIX OS brand: Piri

**Piri** is the PERDIX OS mascot: a grey partridge (*Perdix perdix*), drawn as a stylised
silhouette with feather cut-outs.

## Source files

| File | What it is |
|---|---|
| `piri-master.png` | The master artwork: red on white, 1024×1024. Every other variant is generated from it. |
| `piri-reference-variants.webp` | Reference sheet from the designer: orange / mono, flat and neon-glow versions on dark grey. |
| `generated/` | Output of `python3 tools/make-brand-assets.py`. Don't edit these by hand; regenerate. |

## Colours

| Name | Hex | Use |
|---|---|---|
| Turkish red | `#E30A17` | The Turkish flag red: the red-and-white "standard OS" boot logo. |
| Master red | `#9E1A20` | The red in the master artwork (darker than the flag red). |
| Neon orange | `#FF6A1A` | Boot/shutdown glow, colour logo on dark backgrounds. |
| White | `#FFFFFF` | Neon-white glow, the bird's belly on dark backgrounds. |
| Near black | `#0C0C0E` | Mono logo lines, boot background. |

## Where each version goes

| Place | Version | Generated file |
|---|---|---|
| First screen when the standard OS boots | Red-and-white "Turkish flag" logo on white | `boot-turkish-1080p.png`, `piri-turkish-red.png` |
| Boot and shutdown screens | Logo glowing **orange or white neon** on a black or grey background while the system loads | `piri-neon-orange.png`, `piri-neon-white.png`, `boot-neon-orange-1080p.png` |
| Taskbar and start-menu icon | Monochrome (black, grey, white) or one solid colour, **filled silhouette** so it stays recognisable at 16×16 and 24×24 | `piri-icon-{white,black}-{16,24,32,48,64,128,256}.png` |
| "About System" and Control Panel | Large, high-resolution, next to the system version, architecture and the logo signature | `piri-orange.png`, `piri-mono-dark.png` |
| Login / lock screen | Like a seal: centred or in the lower part of the screen | `piri-neon-white.png` (or orange), smaller |

Rules:
- Small sizes (≤ 48 px) use the filled silhouette, not the line artwork: the feather lines
  turn to noise below that.
- Keep the bird facing left, as drawn. Don't stretch it, rotate it or add outlines.
- On dark backgrounds use the two-tone versions (coloured lines + white belly), as in the
  reference sheet. The plain red artwork is for white backgrounds only.
- The PyQt5 desktop can recolour the logo itself from `piri-strokes-mask.png` and
  `piri-silhouette-mask.png` (white on transparent).

## Where it is used today

| Place | Status |
|---|---|
| Boot splash | Plymouth theme `rootfs/usr/share/plymouth/themes/perdix`: red-and-white Piri on white for 1.5 s, fades to black with Piri glowing orange (pulsing). |
| Shutdown / reboot | Same theme: black with the glowing orange Piri. |
| Desktop centre | Piri in the desktop's neon blue (recoloured from the masks), with a pulsing glow. |
| Desktop top-left | Piri silhouette icon + "PERDIX"; opens About System (the start-menu position). |
| About System | Large Piri, the signature "Piri · *Perdix perdix*", version, edition, kernel, architecture, CPU, GPU, memory. |
| Login / lock screen | Not built yet: the kiosk logs in automatically. |

After changing `piri-master.png`, run `python3 tools/make-brand-assets.py` and commit the results.
