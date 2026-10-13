"""Watch one key or mouse button on every input device with python-evdev (works inside games,
whichever window has focus). Needs read access to /dev/input/event* (the 'input' group).

Only the chosen code is looked at; nothing else that is typed is kept or logged.
"""

import os
import select


def evdev_available() -> tuple[bool, str]:
    try:
        import evdev  # noqa: F401
    except ImportError:
        return False, "Needs python-evdev."
    if not any(os.access(f"/dev/input/{name}", os.R_OK)
               for name in (os.listdir("/dev/input") if os.path.isdir("/dev/input") else [])
               if name.startswith("event")):
        return False, "Needs read access to /dev/input (the 'input' group)."
    return True, "Ready."


def key_name(code: int) -> str:
    from evdev import ecodes
    name = ecodes.KEY.get(code) or ecodes.BTN.get(code) or f"CODE_{code}"
    return name[0] if isinstance(name, list) else name


def _devices():
    from evdev import InputDevice, ecodes, list_devices
    devices = []
    for path in list_devices():
        try:
            device = InputDevice(path)
        except OSError:
            continue
        if ecodes.EV_KEY in device.capabilities():
            devices.append(device)
        else:
            device.close()
    return devices


def watch(on_event, should_stop, code: int | None = None, poll: float = 0.2) -> None:
    """Call on_event(code, pressed) for key/button presses and releases until should_stop().

    With `code`, only that key is reported. Auto-repeat events are ignored.
    """
    from evdev import ecodes
    devices = _devices()
    try:
        by_fd = {d.fd: d for d in devices}
        while not should_stop() and by_fd:
            ready, _, _ = select.select(list(by_fd), [], [], poll)
            for fd in ready:
                try:
                    events = list(by_fd[fd].read())
                except OSError:  # device unplugged
                    by_fd.pop(fd).close()
                    continue
                for event in events:
                    if event.type == ecodes.EV_KEY and event.value in (0, 1) and (code is None or event.code == code):
                        on_event(event.code, event.value == 1)
    finally:
        for device in devices:
            device.close()
