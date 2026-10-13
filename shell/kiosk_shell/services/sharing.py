"""Screen and audio sharing for Discord, OBS and browsers.

Screen: PERDIX OS runs Xorg, where Discord, OBS (XSHM / Xcomposite capture) and browsers capture the
screen and windows directly. The desktop portal (xdg-desktop-portal) is still installed for Flatpak
apps and file dialogs; its ScreenCast part is what Wayland desktops need instead.

Audio: Discord and browsers share a microphone, not the game. The "stream mix" is a PipeWire virtual
microphone, "PERDIX Stream Mix", that carries the game sound (the monitor of the default output)
plus the real microphone; pick it as the input device in Discord, OBS or the browser. It is a
null-audio-sink node created with `pw-cli` (object.linger keeps it after pw-cli exits) and fed with
`pw-link` links; nothing goes through EasyEffects or adds latency to what you hear.

Links point at specific devices, so `follow()` (started by kiosk-session) re-links every few seconds
when the default output or microphone changes, e.g. when Bluetooth headphones connect.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

from kiosk_shell.services import hub_settings

MIX_NAME = "perdix-stream-mix"
MIX_DESCRIPTION = "PERDIX Stream Mix"
TIMEOUT = 5
FOLLOW_SECONDS = 3
DEFAULTS = {"enabled": False, "game": True, "mic": True}


class SharingError(Exception):
    """A sharing action that failed, with a message meant for the user."""


def run(*argv: str, timeout: int = TIMEOUT) -> str:
    if shutil.which(argv[0]) is None:
        raise SharingError(f"{argv[0]} is not installed (PipeWire tools).")
    try:
        result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SharingError("The sound service (PipeWire) did not answer in time.") from None
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if "File exists" in message:  # pw-link: the link is already there
            return ""
        if "connect" in message.lower():
            raise SharingError("The sound service (PipeWire) is not running.")
        raise SharingError(message or f"{argv[0]} failed (exit code {result.returncode}).")
    return result.stdout


# --- reading the PipeWire graph -------------------------------------------------------------------------


@dataclass
class Port:
    id: int
    name: str  # monitor_FL, capture_MONO, input_FR, ...
    channel: str  # FL, FR, MONO, ...
    direction: str  # "in" or "out"


@dataclass
class Node:
    id: int
    name: str
    description: str
    media_class: str
    app: str = ""  # application.name for streams
    ports: list[Port] = field(default_factory=list)

    def outputs(self) -> list[Port]:
        return [p for p in self.ports if p.direction == "out"]

    def inputs(self) -> list[Port]:
        return [p for p in self.ports if p.direction == "in"]


@dataclass
class Link:
    out_node: str
    out_port: str
    in_node: str
    in_port: str
    in_id: int = 0  # the receiving node's id (stream names are not unique)


@dataclass
class Graph:
    nodes: dict[str, Node]  # devices by node.name
    streams: list[Node] = field(default_factory=list)  # apps playing or recording
    default_sink: str = ""
    default_source: str = ""
    links: list[Link] = field(default_factory=list)

    @property
    def mix(self) -> Node | None:
        return self.nodes.get(MIX_NAME)

    def by_id(self, node_id: int) -> Node | None:
        return next((n for n in self.nodes.values() if n.id == node_id), None)


def _default(obj: dict, key: str) -> str:
    for entry in obj.get("metadata") or []:
        if entry.get("key") == key:
            value = entry.get("value")
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    return ""
            if isinstance(value, dict):
                return value.get("name", "")
    return ""


def parse_graph(dump: list) -> Graph:
    """Nodes with their ports, the default devices and the links, from `pw-dump` output."""
    nodes_by_id: dict[int, Node] = {}
    ports, links, graph = [], [], Graph({})
    for obj in dump:
        kind = obj.get("type", "")
        props = (obj.get("info") or {}).get("props") or {}
        if kind == "PipeWire:Interface:Node":
            name = props.get("node.name", "")
            nodes_by_id[obj["id"]] = Node(obj["id"], name, props.get("node.description") or props.get("node.nick")
                                          or props.get("application.name") or name, props.get("media.class", ""),
                                          props.get("application.name", ""))
        elif kind == "PipeWire:Interface:Port":
            name = props.get("port.name", "")
            channel = props.get("audio.channel") or name.rpartition("_")[2]
            ports.append((props.get("node.id"), Port(obj["id"], name, channel, props.get("port.direction", ""))))
        elif kind == "PipeWire:Interface:Link":
            info = obj.get("info") or {}
            links.append((info.get("output-node-id"), info.get("output-port-id"),
                          info.get("input-node-id"), info.get("input-port-id")))
        elif kind == "PipeWire:Interface:Metadata" and (obj.get("props") or {}).get("metadata.name") == "default":
            graph.default_sink = _default(obj, "default.audio.sink")
            graph.default_source = _default(obj, "default.audio.source")
    for node_id, port in ports:
        if node_id in nodes_by_id:
            nodes_by_id[node_id].ports.append(port)
    port_names = {p.id: p.name for _, p in ports}
    for out_node, out_port, in_node, in_port in links:
        if out_node in nodes_by_id and in_node in nodes_by_id:
            graph.links.append(Link(nodes_by_id[out_node].name, port_names.get(out_port, ""),
                                    nodes_by_id[in_node].name, port_names.get(in_port, ""), in_node))
    graph.nodes = {n.name: n for n in nodes_by_id.values() if not n.media_class.startswith("Stream/")}
    graph.streams = [n for n in nodes_by_id.values() if n.media_class.startswith("Stream/")]
    return graph


def graph() -> Graph:
    try:
        return parse_graph(json.loads(run("pw-dump")))
    except ValueError:
        raise SharingError("Could not read the sound devices from PipeWire.") from None


# --- the stream mix ------------------------------------------------------------------------------------------


def side(channel: str) -> tuple[str, ...]:
    """Which stream-mix input(s) a channel feeds: left channels left, right channels right, a mono or
    centre channel both. The LFE (subwoofer) channel is left out."""
    if channel in ("MONO", "FC", "UNK", ""):
        return ("FL", "FR")
    if channel == "LFE":
        return ()
    if channel.endswith("L"):
        return ("FL",)
    if channel.endswith("R"):
        return ("FR",)
    return ("FL", "FR")


def wanted_links(g: Graph, game: bool, mic: bool) -> list[Link]:
    """Links that carry the default output's sound (its monitor ports) and the default microphone."""
    mix = g.mix
    if mix is None:
        return []
    inputs = {p.channel: p.name for p in mix.inputs()}
    wanted = []
    sources = []
    sink = g.nodes.get(g.default_sink)
    if game and sink and sink.name != MIX_NAME and sink.media_class == "Audio/Sink":
        sources.append((sink, [p for p in sink.outputs() if p.name.startswith("monitor_")]))
    source = g.nodes.get(g.default_source)
    # A default "source" can be another output's monitor when no microphone is plugged in; never the
    # stream mix itself (that would feed it back into itself).
    if mic and source and source.name != MIX_NAME and source.media_class.startswith("Audio/Source"):
        sources.append((source, source.outputs()))
    for node, ports in sources:
        for port in ports:
            for channel in side(port.channel):
                if channel in inputs:
                    wanted.append(Link(node.name, port.name, MIX_NAME, inputs[channel]))
    return wanted


def create_mix() -> None:
    run("pw-cli", "create-node", "adapter",
        "{ factory.name=support.null-audio-sink node.name=" + MIX_NAME
        + f' node.description="{MIX_DESCRIPTION}" media.class=Audio/Source/Virtual audio.position=[FL FR]'
        + " object.linger=true priority.session=1 priority.driver=1 }")
    for _ in range(20):  # the ports show up a moment later
        mix = graph().mix
        if mix and len(mix.inputs()) >= 2:
            return
        time.sleep(0.1)
    raise SharingError("PipeWire did not create the stream mix.")


def remove_mix() -> str:
    mix = graph().mix
    if mix is not None:
        run("pw-cli", "destroy", str(mix.id))
    return "Stream mix removed."


@dataclass
class MixStatus:
    exists: bool
    game_from: str = ""  # description of the output whose sound goes in
    mic_from: str = ""

    def text(self) -> str:
        if not self.exists:
            return "Stream mix is off."
        parts = ([f"game sound from {self.game_from}"] if self.game_from else []) + (
            [f"microphone {self.mic_from}"] if self.mic_from else [])
        return f"{MIX_DESCRIPTION} carries " + (" + ".join(parts) if parts else "nothing yet") + "."


def mix_status(g: Graph) -> MixStatus:
    if g.mix is None:
        return MixStatus(False)
    feeding = {link.out_node for link in g.links if link.in_node == MIX_NAME}
    game = next((g.nodes[n].description for n in feeding if n in g.nodes and g.nodes[n].media_class == "Audio/Sink"), "")
    mic = next((g.nodes[n].description for n in feeding if n in g.nodes and g.nodes[n].media_class != "Audio/Sink"), "")
    return MixStatus(True, game, mic)


def sync(game: bool = True, mic: bool = True) -> MixStatus:
    """Create the stream mix if needed and make its links match the current default devices."""
    g = graph()
    if g.mix is None:
        create_mix()
        g = graph()
    wanted = wanted_links(g, game, mic)
    key = lambda link: (link.out_node, link.out_port, link.in_port)  # noqa: E731
    have = {key(link) for link in g.links if link.in_node == MIX_NAME}
    want = {key(link) for link in wanted}
    for out_node, out_port, in_port in have - want:
        run("pw-link", "-d", f"{out_node}:{out_port}", f"{MIX_NAME}:{in_port}")
    for out_node, out_port, in_port in sorted(want - have):
        run("pw-link", f"{out_node}:{out_port}", f"{MIX_NAME}:{in_port}")
    return mix_status(graph())


def settings() -> dict:
    return hub_settings.merged(DEFAULTS, hub_settings.load().get("sharing"))


def apply(values: dict) -> MixStatus:
    """Save the choice and make it so."""
    hub_settings.save_section("sharing", values)
    if not values["enabled"]:
        remove_mix()
        return MixStatus(False)
    return sync(values["game"], values["mic"])


def follow(interval: float = FOLLOW_SECONDS) -> None:
    """Keep the stream mix in step with the settings and the default devices. Runs for the session:
    it stops when kiosk-session, which started it, is gone."""
    parent = os.getppid()
    while os.getppid() == parent:
        try:
            values = settings()
            if values["enabled"]:
                sync(values["game"], values["mic"])
        except SharingError:
            pass  # PipeWire not up yet, or restarting: try again next round
        time.sleep(interval)


# --- what is in place for sharing ------------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool | None  # None: information only
    detail: str


def _process_running(name: str) -> bool:
    try:
        import psutil
        return any(p.info["name"] == name for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def session_type() -> str:
    kind = os.environ.get("XDG_SESSION_TYPE", "")
    if kind:
        return kind
    return "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11" if os.environ.get("DISPLAY") else ""


def checks(g: Graph | None, error: str = "", portals_dir: str = "/usr/share/xdg-desktop-portal/portals",
           runtime_dir: str | None = None) -> list[Check]:
    runtime_dir = runtime_dir or os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    kind = session_type()
    found = []
    if kind == "x11":
        found.append(Check("Screen capture", True, "Xorg: Discord, OBS and browsers capture the screen and windows "
                                                   "directly."))
    elif kind == "wayland":
        found.append(Check("Screen capture", None, "Wayland: apps capture the screen through the desktop portal "
                                                   "(PipeWire ScreenCast)."))
    else:
        found.append(Check("Screen capture", False, "No graphical session found."))
    found.append(Check("PipeWire", g is not None, "running" if g is not None else error or "not running"))
    found.append(Check("WirePlumber", _process_running("wireplumber"),
                       "connects apps to devices" if _process_running("wireplumber") else "not running: apps get no sound"))
    pulse = os.path.exists(os.path.join(runtime_dir, "pulse", "native")) or _process_running("pipewire-pulse")
    found.append(Check("Discord & browsers sound", pulse, "pipewire-pulse is serving PulseAudio apps" if pulse
                       else "pipewire-pulse is not running: Discord and browsers will not hear or record"))
    try:
        portals = sorted(f[:-7] for f in os.listdir(portals_dir) if f.endswith(".portal"))
    except OSError:
        portals = []
    portal = shutil.which("xdg-desktop-portal") or os.path.exists("/usr/lib/xdg-desktop-portal")
    found.append(Check("Desktop portal", bool(portal and portals),
                       f"installed ({', '.join(portals)}); used by Flatpak apps and file dialogs" if portal and portals
                       else "xdg-desktop-portal or its backend is missing"))
    return found


APPS = (
    ("Discord", ("discord", "Discord", "vesktop"),
     "Settings › Voice & Video › Input Device: PERDIX Stream Mix. Share your screen with “Share Your Screen”."),
    ("OBS Studio", ("obs",),
     "Sources: “Screen Capture (XSHM)” or “Window Capture (Xcomposite)”; sound comes from Desktop Audio, "
     "or add “Audio Input Capture” › PERDIX Stream Mix."),
    ("Firefox", ("firefox",), "When a site asks: pick the screen or window, and PERDIX Stream Mix as the microphone."),
    ("Chromium / Chrome", ("chromium", "google-chrome-stable"),
     "When a site asks: pick the screen or window, and PERDIX Stream Mix as the microphone."),
)


def apps() -> list[tuple[str, bool, str]]:
    return [(name, any(shutil.which(b) for b in binaries), tip) for name, binaries, tip in APPS]


def recording_apps(g: Graph) -> list[tuple[str, str]]:
    """(app, device it records from) for every app recording sound right now."""
    found = []
    for node in g.streams:
        if node.media_class != "Stream/Input/Audio":
            continue
        sources = sorted({g.nodes[link.out_node].description for link in g.links
                          if link.in_id == node.id and link.out_node in g.nodes})
        found.append((node.app or node.description, ", ".join(sources) or "nothing"))
    return sorted(found)


if __name__ == "__main__" and sys.argv[1:] == ["--follow"]:
    follow()
