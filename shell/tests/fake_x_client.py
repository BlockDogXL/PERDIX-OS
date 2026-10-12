"""A tiny X11 program for the Task Manager tests: one window that supports _NET_WM_PING and answers
pings the way GTK and Qt do, or, with --frozen, never reads its events (a hung program).

Usage: python fake_x_client.py <display> <title> [--frozen]   (prints the window id when ready)
"""

import os
import sys
import time

from Xlib import X, Xatom, display
from Xlib.protocol import event


def main() -> None:
    name, title, frozen = sys.argv[1], sys.argv[2], "--frozen" in sys.argv
    d = display.Display(name)
    root = d.screen().root
    window = root.create_window(0, 0, 200, 100, 0, d.screen().root_depth)
    atom = d.intern_atom
    window.change_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"), 8, title.encode())
    window.change_property(atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
    window.change_property(atom("WM_PROTOCOLS"), Xatom.ATOM, 32, [atom("_NET_WM_PING"), atom("WM_DELETE_WINDOW")])
    window.map()
    d.sync()
    print(window.id, flush=True)
    if frozen:
        while True:  # never looks at its events, like a program stuck in a long loop
            time.sleep(1)
    while True:
        ev = d.next_event()
        if ev.type == X.ClientMessage and ev.data[1][0] == atom("_NET_WM_PING"):
            reply = event.ClientMessage(window=root, client_type=ev.client_type, data=ev.data)
            root.send_event(reply, event_mask=X.SubstructureNotifyMask | X.SubstructureRedirectMask)
            d.flush()


if __name__ == "__main__":
    main()
