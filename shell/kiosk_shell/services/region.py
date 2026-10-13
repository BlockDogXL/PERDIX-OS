"""Region & Language: time zone and network time (timedatectl), keyboard layout and system language
(localectl), plus setxkbmap so a new keyboard layout works at once in the running session.

The kiosk user may make these changes without a password: the polkit rule allows the timedate1 and
locale1 actions (rootfs/etc/polkit-1/rules.d/50-kiosk-power.rules). The language takes effect for
programs started after the next login; the keyboard and time zone right away.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

TOGGLE_OPTION = "grp:alt_shift_toggle"  # Alt+Shift switches between two layouts, as on Windows
MODEL = "pc105"

# Layouts offered first (layout, variant, name). The full list comes from localectl.
KEYBOARDS = [
    ("tr", "", "Türkçe Q"), ("tr", "f", "Türkçe F"), ("us", "", "English (US)"), ("gb", "", "English (UK)"),
    ("de", "", "Deutsch"), ("fr", "", "Français"), ("es", "", "Español"), ("it", "", "Italiano"),
    ("ru", "", "Русский"), ("az", "", "Azərbaycanca"), ("ar", "", "العربية"),
]
LANGUAGES = {
    "en_US": "English (United States)", "en_GB": "English (United Kingdom)", "tr_TR": "Türkçe",
    "de_DE": "Deutsch", "fr_FR": "Français", "es_ES": "Español", "it_IT": "Italiano", "ru_RU": "Русский",
    "az_AZ": "Azərbaycanca", "ar_SA": "العربية", "C": "Technical (C)",
}


class RegionError(Exception):
    """A setting that could not be changed, with a message meant for the user."""


def run(*argv: str, timeout: int = 15) -> str:
    if shutil.which(argv[0]) is None:
        raise RegionError(f"{argv[0]} is not installed.")
    try:
        result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RegionError(f"{argv[0]} did not answer in time.") from None
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        for prefix in ("Failed to set time zone: ", "Failed to set ntp: ", "Failed to set locale: ",
                       "Failed to set keymap: "):
            message = message.removeprefix(prefix)
        if "Connection refused" in message or "Failed to connect to bus" in message:
            message = "The system settings service is not running."
        elif "Access denied" in message or "Interactive authentication required" in message:
            message = "Not allowed: polkit refused the change."
        raise RegionError(message or f"{argv[0]} failed (exit code {result.returncode}).")
    return result.stdout


# --- reading ------------------------------------------------------------------------------------------


@dataclass
class TimeStatus:
    timezone: str = ""
    ntp: bool = False
    ntp_synced: bool = False
    can_ntp: bool = False
    time: str = ""


def parse_timedatectl_show(text: str) -> TimeStatus:
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    yes = lambda key: values.get(key, "no") == "yes"  # noqa: E731
    return TimeStatus(values.get("Timezone", ""), yes("NTP"), yes("NTPSynchronized"), yes("CanNTP"),
                      values.get("TimeUSec", ""))


@dataclass
class LocaleStatus:
    lang: str = ""
    layouts: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    options: str = ""
    vc_keymap: str = ""


def _split(value: str) -> list[str]:
    return [] if value in ("", "(unset)", "n/a") else value.split(",")


def parse_localectl_status(text: str) -> LocaleStatus:
    status = LocaleStatus()
    for line in text.splitlines():
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if key == "System Locale":
            for part in value.split():
                if part.startswith("LANG="):
                    status.lang = part[5:]
        elif key == "X11 Layout":
            status.layouts = _split(value)
        elif key == "X11 Variant":
            status.variants = _split(value)
        elif key == "X11 Options":
            status.options = "" if value == "(unset)" else value
        elif key == "VC Keymap":
            status.vc_keymap = "" if value == "(unset)" else value
    return status


def parse_setxkbmap_query(text: str) -> LocaleStatus:
    """The keyboard of the running X session (`setxkbmap -query`)."""
    values = {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in text.splitlines()) if v}
    return LocaleStatus("", _split(values.get("layout", "")), values.get("variant", "").split(",")
                        if "variant" in values else [], values.get("options", ""))


@dataclass
class RegionStatus:
    time: TimeStatus
    system: LocaleStatus  # what new sessions get
    session: LocaleStatus | None  # the running X session's keyboard


def status() -> RegionStatus:
    time_status = parse_timedatectl_show(run("timedatectl", "show"))
    system = parse_localectl_status(run("localectl", "status"))
    session = None
    if os.environ.get("DISPLAY") and shutil.which("setxkbmap"):
        try:
            session = parse_setxkbmap_query(run("setxkbmap", "-query"))
        except RegionError:
            session = None
    return RegionStatus(time_status, system, session)


def timezones() -> list[str]:
    return [line.strip() for line in run("timedatectl", "list-timezones").splitlines() if line.strip()]


def locales() -> list[str]:
    return [line.strip() for line in run("localectl", "list-locales").splitlines() if line.strip()]


def language_name(locale: str) -> str:
    code = locale.split(".")[0]
    return LANGUAGES.get(code, code)


def keyboard_name(layout: str, variant: str = "") -> str:
    return next((name for lay, var, name in KEYBOARDS if lay == layout and var == variant),
                f"{layout} ({variant})" if variant else layout)


# --- changing --------------------------------------------------------------------------------------------


def set_timezone(zone: str) -> str:
    if "/" not in zone and zone != "UTC" or ".." in zone or zone.startswith("/"):
        raise RegionError(f"Unknown time zone: {zone}")
    run("timedatectl", "set-timezone", zone)
    return f"Time zone set to {zone.replace('_', ' ')}."


def set_ntp(on: bool) -> str:
    run("timedatectl", "set-ntp", "true" if on else "false")
    return "The clock now sets itself from the internet." if on else "Automatic time is off."


def set_language(locale: str, available: list[str] | None = None) -> str:
    available = locales() if available is None else available
    if locale not in available:
        raise RegionError(f"{locale} is not installed on this system.")
    run("localectl", "set-locale", f"LANG={locale}")
    return f"Language set to {language_name(locale)}. It applies to programs after the next login."


def set_keyboard(primary: tuple[str, str], secondary: tuple[str, str] | None = None) -> str:
    """One layout, or two with Alt+Shift to switch. Applied to the running session at once
    (setxkbmap) and saved for the next boots (localectl, which also sets the console keymap)."""
    pairs = [primary] + ([secondary] if secondary and secondary != primary else [])
    for layout, variant in pairs:
        if not layout.replace("_", "").isalnum() or (variant and not variant.replace("_", "").isalnum()):
            raise RegionError(f"Unknown keyboard layout: {layout}")
    layouts = ",".join(layout for layout, _ in pairs)
    variants = ",".join(variant for _, variant in pairs)
    options = TOGGLE_OPTION if len(pairs) > 1 else ""
    if os.environ.get("DISPLAY") and shutil.which("setxkbmap"):
        # -option "" first: clear the old options instead of adding to them.
        run("setxkbmap", "-layout", layouts, "-variant", variants, "-option", "", *(["-option", options] if options
                                                                                    else []))
    saved = True
    try:
        run("localectl", "set-x11-keymap", layouts, MODEL, variants, options)
    except RegionError:
        saved = False
    names = " + ".join(keyboard_name(*p) for p in pairs)
    switch = " (Alt+Shift switches)" if len(pairs) > 1 else ""
    return (f"Keyboard: {names}{switch}." if saved
            else f"Keyboard: {names}{switch}, for this session only (could not save it for the next boot).")
