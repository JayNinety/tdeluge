#!/usr/bin/env python3
"""
TDeluge – a modern terminal front-end for the Deluge daemon.

  Arch:    sudo pacman -S deluge
  Run:     tdeluge.py            (starts deluged for you if needed)
  Config:  ~/.config/tdeluge/config.ini     (created on first run, press c to reload)
  Log:     ~/.config/tdeluge/tdeluge.log

  tdeluge.py --reset-config      back up config.ini and write a fresh commented one
  tdeluge.py --paths             print config / log / auth paths
"""

from __future__ import annotations

import base64
import codecs
import configparser
import ipaddress
import json
import os
import queue
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import threading
import time
import traceback
import tty
import unicodedata
import urllib.request
from collections import deque
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Paths / logging
# ---------------------------------------------------------------------------

APP_NAME = "TDeluge"
VERSION = "2.0"
HOME = Path.home()
CONFIG_DIR = HOME / ".config" / "tdeluge"
LOG_FILE = CONFIG_DIR / "tdeluge.log"
CONFIG_FILE = CONFIG_DIR / "config.ini"
AUTH_FILE = HOME / ".config" / "deluge" / "auth"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str, also_print: bool = False) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if also_print:
        print(line, file=sys.stderr)


def log_exc(prefix: str = "ERROR") -> None:
    log(prefix + "\n" + traceback.format_exc())


def read_log_tail(n: int = 200) -> list[str]:
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:] if lines else ["(log is empty)"]
    except Exception as e:
        return [f"(read error: {e})"]


def trim_log(max_bytes: int = 1_000_000, keep_lines: int = 500) -> None:
    """Keep the log from growing forever."""
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > max_bytes:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            LOG_FILE.write_text("\n".join(lines[-keep_lines:]) + "\n", encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# config.ini
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = """\
# ════════════════════════════════════════════════════════════════════
#  TDeluge configuration
#
#  • Save this file, then press  c  inside TDeluge to reload it live
#    (colors, layout and daemon settings are re-applied instantly).
#  • Comments must sit on their own line - a '#' inside a value is part
#    of the value (that is how hex colors work).
#  • Delete this file (or run  tdeluge.py --reset-config)  to get a
#    fresh copy with every option documented.
# ════════════════════════════════════════════════════════════════════

[general]
# Where new torrents are saved.
download_dir = ~/Downloads/TDeluge
# Folder the .torrent browser (t) opens in.
browse_dir = ~
# Seconds between list refreshes.
refresh_interval = 1.2
# Daemon connection. Leave username/password empty to use the
# 'localclient' entry of ~/.config/deluge/auth automatically.
host = 127.0.0.1
port = 58846
username =
password =
# Start 'deluged' for you when it is not running (localhost only).
autostart_daemon = true
# When adding a torrent, show the file picker (like deluge-gtk):
#   multi  = only when the torrent contains more than one file
#   always = also for single-file torrents
#   never  = skip it, download everything
file_picker = multi
# Seconds to wait for a magnet link's file list before adding it as-is.
magnet_timeout = 45
# Default list order: added | name | progress | size | down | up | state
sort = added
sort_descending = true

[network]
# Seconds between public-IP checks (0 = only at start and when you press i).
ip_check_interval = 60
# Plain-text "what is my IP" endpoints, tried in order.
ip_services = https://api.ipify.org, https://ipv4.icanhazip.com, https://checkip.amazonaws.com, https://ifconfig.me/ip
# Look up city / ISP of that IP (ipinfo.io) so you can see the VPN is working.
geo_lookup = true
# Hide the last part of the IP in the header (screenshots, streaming).
redact_ip = false

[vpn]
# Network interface name prefixes that count as "a VPN is up".
interfaces = tun, tap, wg, nordlynx, proton, pvpn, mullvad, vpn
# Pause every torrent the moment no VPN interface is detected, and refuse
# to add or resume anything until it is back.
kill_switch = false
# Resume everything automatically once the VPN returns.
kill_switch_auto_resume = false

[deluge]
# Options applied to every torrent you add from TDeluge.
add_paused = false
auto_managed = true
sequential_download = false
prioritize_first_last_pieces = false
super_seeding = false
pre_allocate_storage = false
# -1 = unlimited. Speeds are KiB/s.
max_connections = -1
max_upload_slots = -1
max_download_speed = -1
max_upload_speed = -1
# Stop (or remove) a torrent once it reaches this share ratio.
stop_at_ratio = false
stop_ratio = 2.0
remove_at_ratio = false
# Move finished downloads somewhere else.
move_completed = false
move_completed_path =

[session]
# Daemon-wide settings, pushed to deluged when TDeluge starts / reloads.
# EMPTY = leave whatever Deluge already has. Only fill in what you want to force.
#
# Bind Deluge to your VPN so nothing can leak if it drops:
#   outgoing_interface = tun0      (or wg0, nordlynx, ...)
#   listen_interface   = 10.8.0.2  (Deluge 2.x wants an IP address here)
outgoing_interface =
listen_interface =
# true / false switches
dht =
upnp =
natpmp =
utpex =
lsd =
# Encryption: policies 0 = forced, 1 = enabled, 2 = disabled
#             level    0 = handshake, 1 = full stream, 2 = either
enc_in_policy =
enc_out_policy =
enc_level =
# Global limits (KiB/s, -1 = unlimited)
max_download_speed =
max_upload_speed =
max_connections_global =

[ui]
# Color preset: amethyst | ocean | ember | forest | rose | nord | mono
theme = amethyst
# card = two-line torrents with progress bars, compact = one line each
layout = card
# block | thin | dots
bar_style = block
# Blank lines between cards (0 or 1).
card_spacing = 1
# Speed sparklines in the header.
show_graphs = true
graph_width = 12
# Open the details panel for the selected torrent by default.
details_open = false
# Spinners and blinking cursor.
animations = true
# rounded | square | heavy | double | ascii
border = rounded

[colors]
# Override single colors of the preset. Formats: #RRGGBB, RRGGBB or r,g,b
# Available: accent accent2 ok warn err down up text text_dim text_faint
#            border sel_bg sel_fg bar_empty key_bg key_fg pill_fg
# Examples (remove the leading # to use them):
# accent = #a78bfa
# accent2 = #f472b6
# border = #5a4075
# sel_bg = #3d2c52
"""


def _bool(v: str) -> bool:
    s = v.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise ValueError("expected true/false")


def _choice(*opts: str) -> Callable[[str], str]:
    def f(v: str) -> str:
        s = v.strip().lower()
        if s not in opts:
            raise ValueError("expected one of: " + ", ".join(opts))
        return s
    return f


def parse_color(s: str) -> tuple[int, int, int]:
    s = s.strip()
    m = re.fullmatch(r"(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", s)
    if m:
        r, g, b = (int(x) for x in m.groups())
        if max(r, g, b) > 255:
            raise ValueError("rgb out of range")
        return r, g, b
    s = s.lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", s):
        s = "".join(c * 2 for c in s)
    if re.fullmatch(r"[0-9a-fA-F]{6}", s):
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    raise ValueError("bad color")


# name, type, default  – options sent with every add_torrent
TORRENT_OPTS: list[tuple[str, Callable[[str], Any], Any]] = [
    ("add_paused", _bool, False),
    ("auto_managed", _bool, True),
    ("sequential_download", _bool, False),
    ("prioritize_first_last_pieces", _bool, False),
    ("super_seeding", _bool, False),
    ("pre_allocate_storage", _bool, False),
    ("max_connections", int, -1),
    ("max_upload_slots", int, -1),
    ("max_download_speed", float, -1.0),
    ("max_upload_speed", float, -1.0),
    ("stop_at_ratio", _bool, False),
    ("stop_ratio", float, 2.0),
    ("remove_at_ratio", _bool, False),
    ("move_completed", _bool, False),
]

# only pushed to the daemon when the user filled them in
SESSION_OPTS: list[tuple[str, Callable[[str], Any]]] = [
    ("outgoing_interface", str), ("listen_interface", str),
    ("dht", _bool), ("upnp", _bool), ("natpmp", _bool), ("utpex", _bool), ("lsd", _bool),
    ("enc_in_policy", int), ("enc_out_policy", int), ("enc_level", int),
    ("max_download_speed", float), ("max_upload_speed", float),
    ("max_connections_global", int),
]

SORT_FIELDS = ["added", "name", "progress", "size", "down", "up", "state"]


class Config:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.load()

    def load(self) -> None:
        self.warnings = []
        if not CONFIG_FILE.exists():
            try:
                CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
                log(f"wrote default config: {CONFIG_FILE}")
            except Exception as e:
                log(f"could not write config: {e}")
        cp = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
        try:
            cp.read(CONFIG_FILE, encoding="utf-8")
        except Exception as e:
            self.warnings.append(f"config.ini: {e}")
            log(f"config read: {e}")

        def get(sec: str, key: str, default: Any, cast: Callable[[str], Any] = str) -> Any:
            try:
                v = cp.get(sec, key).strip()
            except (configparser.NoSectionError, configparser.NoOptionError):
                return default
            if v == "":
                return default
            try:
                return cast(v)
            except Exception as e:
                self.warnings.append(f"[{sec}] {key} = {v!r}: {e}")
                return default

        g = "general"
        self.download_dir = Path(get(g, "download_dir", "~/Downloads/TDeluge")).expanduser()
        self.browse_dir = Path(get(g, "browse_dir", "~")).expanduser()
        self.refresh = max(0.3, get(g, "refresh_interval", 1.2, float))
        self.host = get(g, "host", "127.0.0.1")
        self.port = get(g, "port", 58846, int)
        self.username = get(g, "username", "")
        self.password = get(g, "password", "")
        self.autostart = get(g, "autostart_daemon", True, _bool)
        self.file_picker = get(g, "file_picker", "multi", _choice("multi", "always", "never"))
        self.magnet_timeout = max(5, get(g, "magnet_timeout", 45, int))
        self.sort = get(g, "sort", "added", _choice(*SORT_FIELDS))
        self.sort_desc = get(g, "sort_descending", True, _bool)

        n = "network"
        self.ip_interval = max(0, get(n, "ip_check_interval", 60, int))
        svc = get(n, "ip_services", "")
        self.ip_services = [s.strip() for s in svc.split(",") if s.strip().startswith("http")] or [
            "https://api.ipify.org", "https://ipv4.icanhazip.com",
            "https://checkip.amazonaws.com", "https://ifconfig.me/ip",
        ]
        self.geo = get(n, "geo_lookup", True, _bool)
        self.redact_ip = get(n, "redact_ip", False, _bool)

        v = "vpn"
        pre = get(v, "interfaces", "tun, tap, wg, nordlynx, proton, pvpn, mullvad, vpn")
        self.vpn_prefixes = [p.strip().lower() for p in pre.split(",") if p.strip()]
        self.kill_switch = get(v, "kill_switch", False, _bool)
        self.kill_resume = get(v, "kill_switch_auto_resume", False, _bool)

        self.topts: dict[str, Any] = {k: get("deluge", k, d, c) for k, c, d in TORRENT_OPTS}
        self.move_completed_path = get("deluge", "move_completed_path", "")

        self.session: dict[str, Any] = {}
        for key, cast in SESSION_OPTS:
            val = get("session", key, None, cast)
            if val is not None:
                self.session[key] = val

        u = "ui"
        self.theme = get(u, "theme", "amethyst", _choice(*PRESETS))
        self.layout = get(u, "layout", "card", _choice("card", "compact"))
        self.bar_style = get(u, "bar_style", "block", _choice("block", "thin", "dots"))
        self.spacing = min(1, max(0, get(u, "card_spacing", 1, int)))
        self.graphs = get(u, "show_graphs", True, _bool)
        self.graph_width = min(40, max(4, get(u, "graph_width", 12, int)))
        self.details_open = get(u, "details_open", False, _bool)
        self.animations = get(u, "animations", True, _bool)
        self.border = get(u, "border", "rounded", _choice(*BORDERS))

        overrides: dict[str, tuple[int, int, int]] = {}
        for key in THEME_KEYS:
            val = get("colors", key, None, parse_color)
            if val is not None:
                overrides[key] = val
        apply_theme(self.theme, overrides, self.border)

    def torrent_options(self, extra: dict | None = None) -> dict:
        o = dict(self.topts)
        o["download_location"] = str(self.download_dir)
        if o.get("move_completed") and self.move_completed_path:
            o["move_completed_path"] = str(Path(self.move_completed_path).expanduser())
        else:
            o["move_completed"] = False
        if extra:
            o.update(extra)
        return o

    def minimal_options(self, extra: dict | None = None) -> dict:
        o = {
            "download_location": str(self.download_dir),
            "add_paused": self.topts["add_paused"],
            "auto_managed": self.topts["auto_managed"],
        }
        if extra:
            o.update(extra)
        return o


# ---------------------------------------------------------------------------
# Theme / terminal primitives
# ---------------------------------------------------------------------------

RGB = tuple[int, int, int]

ALT_ENTER = "\033[?1049h"
ALT_EXIT = "\033[?1049l"
HIDE_CUR = "\033[?25l"
SHOW_CUR = "\033[?25h"
PASTE_ON = "\033[?2004h"
PASTE_OFF = "\033[?2004l"
CLEAR = "\033[2J\033[H"
RESET = "\033[0m"
SYNC_ON = "\033[?2026h"
SYNC_OFF = "\033[?2026l"

THEME_KEYS = (
    "accent", "accent2", "ok", "warn", "err", "down", "up", "text", "text_dim",
    "text_faint", "border", "sel_bg", "sel_fg", "bar_empty", "key_bg", "key_fg", "pill_fg",
)


def _p(**kw: str) -> dict[str, RGB]:
    return {k: parse_color(v) for k, v in kw.items()}


_BASE = dict(ok="#7ed957", warn="#e0c06a", err="#ef6b6b", pill_fg="#120d1c")
PRESETS: dict[str, dict[str, RGB]] = {
    "amethyst": _p(**{**_BASE, "accent": "#a78bfa", "accent2": "#f472b6", "down": "#7dd3fc",
                      "up": "#86efac", "text": "#e8e4f0", "text_dim": "#9a92a8",
                      "text_faint": "#5f5873", "border": "#5a4075", "sel_bg": "#3d2c52",
                      "sel_fg": "#f5f0ff", "bar_empty": "#352c47", "key_bg": "#5a4075",
                      "key_fg": "#f5f0ff"}),
    "ocean": _p(**{**_BASE, "accent": "#5eead4", "accent2": "#60a5fa", "down": "#7dd3fc",
                   "up": "#86efac", "text": "#e2eef5", "text_dim": "#8fa6b5",
                   "text_faint": "#4f6572", "border": "#1f5f7a", "sel_bg": "#163f55",
                   "sel_fg": "#f0fbff", "bar_empty": "#1a3341", "key_bg": "#1f5f7a",
                   "key_fg": "#f0fbff"}),
    "ember": _p(**{**_BASE, "accent": "#fb923c", "accent2": "#f43f5e", "down": "#fcd34d",
                   "up": "#a3e635", "text": "#f3e9e2", "text_dim": "#a8948a",
                   "text_faint": "#6b5850", "border": "#7a3a1e", "sel_bg": "#4a2616",
                   "sel_fg": "#fff4ec", "bar_empty": "#3a2219", "key_bg": "#8a4120",
                   "key_fg": "#fff4ec"}),
    "forest": _p(**{**_BASE, "accent": "#86efac", "accent2": "#fde047", "down": "#6ee7b7",
                    "up": "#bef264", "text": "#e6f0e8", "text_dim": "#8fa795",
                    "text_faint": "#53665a", "border": "#2f6b45", "sel_bg": "#1f4a31",
                    "sel_fg": "#f2fff5", "bar_empty": "#1d3326", "key_bg": "#2f6b45",
                    "key_fg": "#f2fff5"}),
    "rose": _p(**{**_BASE, "accent": "#f9a8d4", "accent2": "#c4b5fd", "down": "#fbcfe8",
                  "up": "#a7f3d0", "text": "#f5e8ef", "text_dim": "#a78c9c",
                  "text_faint": "#6b5563", "border": "#7a3a5a", "sel_bg": "#4d2440",
                  "sel_fg": "#fff0f7", "bar_empty": "#38212f", "key_bg": "#8a4268",
                  "key_fg": "#fff0f7"}),
    "nord": _p(accent="#88c0d0", accent2="#b48ead", ok="#a3be8c", warn="#ebcb8b", err="#bf616a",
               down="#81a1c1", up="#a3be8c", text="#eceff4", text_dim="#a0a9ba",
               text_faint="#5d6879", border="#4c566a", sel_bg="#3b4252", sel_fg="#eceff4",
               bar_empty="#2e3440", key_bg="#4c566a", key_fg="#eceff4", pill_fg="#2e3440"),
    "mono": _p(accent="#f0f0f0", accent2="#a8a8a8", ok="#d8d8d8", warn="#bcbcbc", err="#ff7b7b",
               down="#d0d0d0", up="#b0b0b0", text="#e6e6e6", text_dim="#8c8c8c",
               text_faint="#555555", border="#555555", sel_bg="#2e2e2e", sel_fg="#ffffff",
               bar_empty="#2a2a2a", key_bg="#555555", key_fg="#ffffff", pill_fg="#111111"),
}

BORDERS = {
    "rounded": "╭╮╰╯─│├┤",
    "square": "┌┐└┘─│├┤",
    "heavy": "┏┓┗┛━┃┣┫",
    "double": "╔╗╚╝═║╠╣",
    "ascii": "++++-|++",
}


class Theme:
    border_style = "rounded"


T = Theme()


def apply_theme(name: str, overrides: dict[str, RGB] | None = None, border: str = "rounded") -> None:
    base = PRESETS.get(name) or PRESETS["amethyst"]
    for k in THEME_KEYS:
        setattr(T, k, (overrides or {}).get(k) or base[k])
    T.border_style = border if border in BORDERS else "rounded"


apply_theme("amethyst")


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def sgr(fgc: RGB | None = None, bgc: RGB | None = None, bold: bool = False,
        italic: bool = False, dim: bool = False) -> str:
    codes = ["0"]
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if italic:
        codes.append("3")
    if fgc:
        codes.append(f"38;2;{fgc[0]};{fgc[1]};{fgc[2]}")
    if bgc:
        codes.append(f"48;2;{bgc[0]};{bgc[1]};{bgc[2]}")
    return "\033[" + ";".join(codes) + "m"


def term_size() -> tuple[int, int]:
    s = shutil.get_terminal_size((100, 30))
    return s.columns, s.lines


@lru_cache(maxsize=8192)
def _cw(ch: str) -> int:
    o = ord(ch)
    if o < 32 or o == 0x7F:
        return 0
    if ch in "\u200b\u200d\ufe0e\ufe0f" or unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def cw(s: str) -> int:
    return sum(_cw(c) for c in s)


def clip(s: str, w: int) -> str:
    if w <= 0:
        return ""
    out, cur = [], 0
    for ch in s:
        c = _cw(ch)
        if cur + c > w:
            break
        out.append(ch)
        cur += c
    return "".join(out)


def fit(s: str, w: int) -> str:
    """Truncate with an ellipsis."""
    if w <= 0:
        return ""
    if cw(s) <= w:
        return s
    return clip(s, w - 1) + "…"


def clean(s: Any) -> str:
    """Make arbitrary text (torrent names!) safe to print."""
    return "".join(c if c.isprintable() else " " for c in str(s))


def human_size(n: float) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024 or u == "PB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024.0
    return "?"


def human_rate(n: float) -> str:
    return human_size(n) + "/s"


def human_eta(sec: Any) -> str:
    try:
        s = int(sec)
    except Exception:
        return "—"
    if s <= 0:
        return "—"
    if s >= 365 * 86400:
        return "∞"
    d, r = divmod(s, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


SPARK = "▁▁▂▃▄▅▆▇█"
PART = " ▏▎▍▌▋▊▉"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Line:
    """A single styled terminal line with a hard width limit."""

    __slots__ = ("w", "bg", "parts", "used")

    def __init__(self, w: int, bg: RGB | None = None) -> None:
        self.w, self.bg, self.parts, self.used = max(0, w), bg, [], 0

    def add(self, text: str, fg: RGB | None = None, bold: bool = False, bg: RGB | None = None,
            italic: bool = False, dim: bool = False) -> "Line":
        if not text or self.used >= self.w:
            return self
        t = clip(text, self.w - self.used)
        self.parts.append(sgr(fg, bg or self.bg, bold, italic, dim) + t)
        self.used += cw(t)
        return self

    def grad(self, text: str, c1: RGB, c2: RGB, bold: bool = False) -> "Line":
        n = max(1, len(text) - 1)
        for i, ch in enumerate(text):
            self.add(ch, fg=lerp(c1, c2, i / n), bold=bold)
        return self

    def spark(self, vals: Any, width: int, lo: RGB, hi: RGB, peak_min: float = 10240.0) -> "Line":
        v = list(vals)[-width:]
        v = [0.0] * (width - len(v)) + v
        peak = max(max(v), peak_min)
        for x in v:
            lvl = 0 if x <= 0 else min(8, max(1, int(x / peak * 8 + 0.5)))
            self.add(SPARK[lvl], fg=lerp(lo, hi, lvl / 8))
        return self

    def fill_to(self, col: int) -> "Line":
        if col > self.used:
            self.add(" " * (col - self.used))
        return self

    def pad(self) -> "Line":
        return self.fill_to(self.w)

    def extend(self, other: "Line") -> "Line":
        self.parts += other.parts
        self.used += other.used
        return self

    def render(self) -> str:
        return "".join(self.parts) + RESET


class Screen:
    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows, self.out = cols, rows, []

    def put(self, row: int, col: int, content: Any) -> None:
        if row < 1 or row > self.rows or col < 1:
            return
        s = content.render() if isinstance(content, Line) else content
        self.out.append(f"\033[{row};{col}H{s}")


def draw_box(scr: Screen, top: int, left: int, w: int, h: int, title: str = "", right: str = "",
             color: RGB | None = None, fill: bool = True) -> None:
    tl, tr, bl, br, hz, vt, _lt, _rt = BORDERS[T.border_style]
    c = color or T.border
    l = Line(w).add(tl, c)
    if title:
        l.add(hz, c).add(f" {title} ", T.accent, bold=True)
    rem = w - 1 - l.used
    if right and rem > cw(right) + 4:
        rt_txt = f" {right} "
        l.add(hz * (rem - cw(rt_txt) - 1), c).add(rt_txt, T.text_dim).add(hz, c)
    else:
        l.add(hz * rem, c)
    l.add(tr, c)
    scr.put(top, left, l)
    for r in range(top + 1, top + h - 1):
        if fill:
            scr.put(r, left, Line(w).add(vt, c).add(" " * (w - 2)).add(vt, c))
        else:
            scr.put(r, left, Line(1).add(vt, c))
            scr.put(r, left + w - 1, Line(1).add(vt, c))
    scr.put(top + h - 1, left, Line(w).add(bl, c).add(hz * (w - 2), c).add(br, c))


def hsep(scr: Screen, row: int, left: int, w: int, label: str = "") -> None:
    _tl, _tr, _bl, _br, hz, _vt, lt, rt = BORDERS[T.border_style]
    l = Line(w).add(lt, T.border)
    if label:
        l.add(hz, T.border).add(f" {label} ", T.text_dim)
    l.add(hz * (w - 1 - l.used), T.border).add(rt, T.border)
    scr.put(row, left, l)


def scrollbar(scr: Screen, col: int, top: int, height: int, total: int, view: int, pos: int) -> None:
    if total <= view or height <= 0:
        return
    thumb = max(1, round(height * view / total))
    start = round((height - thumb) * pos / max(1, total - view))
    ch = "#" if T.border_style == "ascii" else "┃"
    for i in range(thumb):
        scr.put(top + start + i, col, Line(1).add(ch, T.accent))


def hint_line(items: list[tuple[str, str]], width: int, right: list[tuple[str, str]] | None = None) -> Line:
    l = Line(width).add(" ")
    reserve = 0
    rl = Line(width)
    if right:
        for k, lab in right:
            rl.add(f" {k} ", T.key_fg, bold=True, bg=T.key_bg).add(f" {lab}", T.text_dim).add("  ")
        reserve = rl.used
    for k, lab in items:
        need = cw(k) + 2 + 1 + cw(lab) + 2
        if l.used + need + reserve > width:
            break
        l.add(f" {k} ", T.key_fg, bold=True, bg=T.key_bg).add(f" {lab}", T.text_dim).add("  ")
    if right:
        l.fill_to(width - reserve).extend(rl)
    return l


def draw_bar(l: Line, width: int, frac: float, c1: RGB, c2: RGB, style: str) -> None:
    frac = max(0.0, min(1.0, frac))
    if width <= 0:
        return
    span = max(1, width - 1)
    if style == "thin":
        filled = int(frac * width + 0.5)
        for i in range(width):
            if i < filled:
                l.add("━", lerp(c1, c2, i / span))
            else:
                l.add("─", T.bar_empty)
    elif style == "dots":
        filled = int(frac * width + 0.5)
        for i in range(width):
            if i < filled:
                l.add("●", lerp(c1, c2, i / span))
            else:
                l.add("·", T.bar_empty)
    else:
        total8 = int(frac * width * 8)
        full, part = divmod(total8, 8)
        for i in range(width):
            if i < full:
                l.add("█", lerp(c1, c2, i / span))
            elif i == full and part:
                l.add(PART[part], lerp(c1, c2, i / span))
            else:
                l.add("░", T.bar_empty)


# ---------------------------------------------------------------------------
# Keyboard input
# ---------------------------------------------------------------------------

CSI_MAP = {
    "A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end",
    "Z": "shift+tab", "1~": "home", "7~": "home", "4~": "end", "8~": "end",
    "3~": "delete", "5~": "pgup", "6~": "pgdn",
}
SIMPLE_KEYS = {
    "\r": "enter", "\n": "enter", " ": "space", "\x7f": "backspace", "\b": "backspace",
    "\t": "tab", "\x03": "ctrl+c", "\x15": "ctrl+u", "\x17": "ctrl+w", "\x0c": "ctrl+l",
}


class KeyReader:
    PASTE_END = "\x1b[201~"

    def __init__(self) -> None:
        self.dec = codecs.getincrementaldecoder("utf-8")("ignore")
        self.buf = ""
        self.paste: str | None = None

    def feed(self, data: bytes) -> list[str]:
        buf = self.buf + self.dec.decode(data)
        keys: list[str] = []
        i, n = 0, len(buf)
        while i < n:
            if self.paste is not None:
                end = buf.find(self.PASTE_END, i)
                if end < 0:
                    keep = 0
                    for k in range(min(5, n - i), 0, -1):
                        if self.PASTE_END.startswith(buf[n - k:]):
                            keep = k
                            break
                    self.paste += buf[i:n - keep]
                    i = n - keep
                    break
                self.paste += buf[i:end]
                keys.append("paste:" + self.paste)
                self.paste = None
                i = end + len(self.PASTE_END)
                continue
            ch = buf[i]
            if ch == "\x1b":
                if buf.startswith("\x1b[200~", i):
                    self.paste = ""
                    i += 6
                    continue
                if i + 1 < n and buf[i + 1] in "[O":
                    j = i + 2
                    while j < n and not ("@" <= buf[j] <= "~"):
                        j += 1
                    if j >= n:
                        break  # incomplete sequence, wait for more
                    seq = buf[i + 2:j + 1]
                    key = CSI_MAP.get(seq) or (CSI_MAP.get(seq[-1]) if seq[-1] in "ABCDHF" else "")
                    if key:
                        keys.append(key)
                    i = j + 1
                    continue
                keys.append("esc")
                i += 1
                continue
            keys.append(SIMPLE_KEYS.get(ch, ch))
            i += 1
        self.buf = buf[i:]
        return keys


# ---------------------------------------------------------------------------
# bencode (just enough to list the files inside a .torrent / magnet metadata)
# ---------------------------------------------------------------------------

def bdecode(data: bytes) -> Any:
    def parse(i: int) -> tuple[Any, int]:
        c = data[i:i + 1]
        if c == b"i":
            j = data.index(b"e", i)
            return int(data[i + 1:j]), j + 1
        if c == b"l":
            i += 1
            out = []
            while data[i:i + 1] != b"e":
                v, i = parse(i)
                out.append(v)
            return out, i + 1
        if c == b"d":
            i += 1
            d = {}
            while data[i:i + 1] != b"e":
                k, i = parse(i)
                v, i = parse(i)
                d[k] = v
            return d, i + 1
        if c.isdigit():
            j = data.index(b":", i)
            n = int(data[i:j])
            return data[j + 1:j + 1 + n], j + 1 + n
        raise ValueError("bad bencode")

    v, _ = parse(0)
    return v


def _dec(b: Any) -> str:
    return b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b)


def files_from_info(info: dict) -> tuple[str, list[dict]]:
    """info dict -> (torrent name, [{path,size,hidden}]) using Deluge's path convention."""
    name = clean(_dec(info.get(b"name.utf-8") or info.get(b"name") or b"torrent"))
    files: list[dict] = []
    if b"files" in info:
        for f in info[b"files"]:
            parts = f.get(b"path.utf-8") or f.get(b"path") or [b"?"]
            rel = "/".join(clean(_dec(p)) for p in parts)
            pad = b"p" in (f.get(b"attr") or b"") or ".pad" in rel.split("/")
            files.append({"path": f"{name}/{rel}", "size": int(f.get(b"length") or 0), "hidden": pad})
    else:
        files.append({"path": name, "size": int(info.get(b"length") or 0), "hidden": False})
    return name, files


def parse_torrent_bytes(data: bytes) -> tuple[str, list[dict]] | None:
    try:
        d = bdecode(data)
        info = d[b"info"] if b"info" in d else d
        return files_from_info(info)
    except Exception as e:
        log(f"torrent parse failed: {e}")
        return None


def is_pad(path: str) -> bool:
    parts = path.split("/")
    return ".pad" in parts or parts[-1].startswith("_____padding_file_")


# ---------------------------------------------------------------------------
# Network helpers: public IP, geo, VPN detection
# ---------------------------------------------------------------------------

UA = {"User-Agent": "curl/8.5.0", "Accept": "*/*"}


def http_get(url: str, timeout: float = 5.0, limit: int = 8_000_000) -> bytes:
    err: Exception
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit)
    except Exception as e:
        err = e
    curl = shutil.which("curl")
    if curl:
        p = subprocess.run(
            [curl, "-fsSL", "--max-time", str(int(timeout) + 1), "-A", UA["User-Agent"], url],
            capture_output=True, timeout=timeout + 4,
        )
        if p.returncode == 0 and p.stdout:
            return p.stdout[:limit]
        raise RuntimeError(f"{err}; curl rc={p.returncode} {p.stderr.decode(errors='replace').strip()[:100]}")
    raise err


def lookup_public_ip(services: list[str], geo: bool, prev: dict) -> dict:
    res = {"ip": "", "place": "", "org": "", "err": ""}
    errs: list[str] = []
    for url in services:
        try:
            txt = http_get(url, timeout=4).decode("utf-8", "ignore").strip().split()[0]
            ipaddress.ip_address(txt)
            res["ip"] = txt
            break
        except Exception as e:
            errs.append(f"{url}: {e}")
    if not res["ip"]:
        res["err"] = " | ".join(errs)[:600]
        return res
    if geo:
        if prev.get("ip") == res["ip"] and prev.get("place"):
            res["place"], res["org"] = prev["place"], prev.get("org", "")
        else:
            try:
                j = json.loads(http_get(f"https://ipinfo.io/{res['ip']}/json", timeout=4))
                res["place"] = ", ".join(x for x in (j.get("city"), j.get("country")) if x)
                res["org"] = str(j.get("org") or "")
            except Exception as e:
                log(f"geo lookup failed: {e}")
    return res


def detect_vpn(prefixes: list[str]) -> str:
    try:
        for p in sorted(Path("/sys/class/net").iterdir()):
            name = p.name.lower()
            if any(name.startswith(x) for x in prefixes):
                try:
                    state = (p / "operstate").read_text().strip()
                except Exception:
                    state = "unknown"
                if state not in ("down", "notpresent", "lowerlayerdown"):
                    return p.name
    except Exception:
        pass
    return ""


def redact(ip: str) -> str:
    if "." in ip:
        a = ip.split(".")
        return f"{a[0]}.{a[1]}.•.•" if len(a) == 4 else ip
    if ":" in ip:
        return ":".join(ip.split(":")[:2]) + ":••••"
    return ip


class NetState:
    def __init__(self) -> None:
        self.ip = ""
        self.place = ""
        self.org = ""
        self.err = ""
        self.last = 0.0
        self.busy = False
        self.vpn = ""
        self.ext_ip = ""
        self.bound = ""
        self.listen = ""


# ---------------------------------------------------------------------------
# Deluge backend
# ---------------------------------------------------------------------------

HAS_RPC = False
deluge_client = None
reactor = None

try:
    from deluge.ui.client import client as _client
    from twisted.internet import reactor as _reactor

    deluge_client = _client
    reactor = _reactor
    HAS_RPC = True
    log("RPC client OK")
except Exception as e:
    log(f"RPC import failed: {e}")
    log_exc()


def format_failure(failure: Any) -> str:
    try:
        return (failure.getErrorMessage() or str(failure.value)).replace("\n", " | ")[:400]
    except Exception:
        return repr(failure)


def read_localclient_auth() -> tuple[str, str]:
    if not AUTH_FILE.is_file():
        return "", ""
    try:
        for line in AUTH_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) >= 2 and parts[0] == "localclient":
                return parts[0], parts[1]
    except Exception as e:
        log(f"auth read: {e}")
    return "", ""


def run_console(args: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    cmd = ["deluge-console"] + args
    log(f"CONSOLE: {' '.join(cmd)[:200]}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out, err = (p.stdout or "").strip(), (p.stderr or "").strip()
        if out:
            log(f"stdout: {out[:800]}")
        if err:
            log(f"stderr: {err[:800]}")
        return p.returncode, out, err
    except FileNotFoundError:
        return 127, "", "deluge-console not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        log_exc()
        return 1, "", str(e)


def ensure_deluged(cfg: Config) -> bool:
    if cfg.host not in ("127.0.0.1", "localhost", "::1"):
        return True
    try:
        if subprocess.run(["pgrep", "-x", "deluged"], capture_output=True).returncode == 0:
            log("deluged running")
            return True
    except Exception:
        pass
    if not cfg.autostart:
        return False
    log("Starting deluged…")
    try:
        subprocess.Popen(["deluged"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.0)
        ok = subprocess.run(["pgrep", "-x", "deluged"], capture_output=True).returncode == 0
        log(f"deluged start ok={ok}")
        return ok
    except Exception as e:
        log(f"start failed: {e}")
        return False


STATUS_KEYS = [
    "name", "state", "progress", "total_size", "download_payload_rate", "upload_payload_rate",
    "num_peers", "num_seeds", "total_peers", "total_seeds", "paused", "message", "time_added",
    "eta", "ratio", "save_path", "total_done", "total_wanted", "total_uploaded",
    "tracker_host", "tracker_status", "num_files",
]


class Backend:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.connected = False
        self.mode = "none"
        self.last_error = ""
        self.torrents: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}  # magnets waiting for metadata -> file priorities
        self.ext_ip = ""
        self.bound = ""
        self.listen = ""
        self.next_retry = 0.0

    def err(self, msg: str) -> None:
        self.last_error = msg
        if msg:
            log(f"ERR: {msg}")

    # ----- connection -----
    def connect(self) -> bool:
        ensure_deluged(self.cfg)
        if HAS_RPC:
            for attempt in range(3):
                if self._connect_rpc():
                    self.after_connect()
                    return True
                if "refused" not in self.last_error.lower():
                    break
                time.sleep(1.2)
        return self._connect_console()

    def _connect_rpc(self) -> bool:
        assert deluge_client
        user, password = self.cfg.username, self.cfg.password
        if not user:
            user, password = read_localclient_auth()
        log(f"RPC auth user={user!r} {self.cfg.host}:{self.cfg.port}")
        done = {"ok": False, "err": ""}

        def ok(_: Any) -> None:
            done["ok"] = True

        def fail(f: Any) -> None:
            done["err"] = format_failure(f) or "failed"

        try:
            d = deluge_client.connect(self.cfg.host, self.cfg.port, user or "", password or "")
        except Exception as e:
            self.err(f"RPC: {e}")
            return False
        d.addCallback(ok)
        d.addErrback(fail)
        deadline = time.time() + 6
        while time.time() < deadline and not done["ok"] and not done["err"]:
            reactor.iterate(0.05)  # type: ignore
        if done["ok"]:
            self.connected, self.mode = True, "rpc"
            self.err("")
            log("RPC connected")
            return True
        self.connected = False
        self.err(f"RPC: {done['err'] or 'timeout'}")
        return False

    def _connect_console(self) -> bool:
        rc, out, err = run_console(["info"], timeout=15)
        text = (out + err).lower()
        if rc != 0 or "connection refused" in text or "could not connect" in text or "not found" in text:
            self.err(out or err or "not connected")
            self.connected, self.mode = False, "none"
            return False
        self.connected, self.mode = True, "console"
        self.err("")
        log("Console mode")
        return True

    def after_connect(self) -> None:
        self.apply_session_config()
        self.read_bind_info()

    def maybe_reconnect(self) -> None:
        now = time.monotonic()
        if self.connected or now < self.next_retry:
            return
        self.next_retry = now + 6
        if self.connect():
            self.refresh()

    def apply_session_config(self) -> None:
        if self.mode != "rpc" or not self.cfg.session:
            return
        ok, _ = self.rpc("set_config", dict(self.cfg.session))
        log(f"session config applied ok={ok}: {self.cfg.session}")

    def read_bind_info(self) -> None:
        if self.mode != "rpc":
            return
        ok, v = self.rpc("get_config_values", ["outgoing_interface", "listen_interface"], quiet=True, timeout=3)
        if ok and isinstance(v, dict):
            self.bound = str(v.get("outgoing_interface") or "")
            self.listen = str(v.get("listen_interface") or "")

    def update_external_ip(self) -> None:
        if self.mode != "rpc":
            return
        ok, v = self.rpc("get_external_ip", quiet=True, timeout=2)
        if ok and v:
            try:
                self.ext_ip = str(ipaddress.ip_address(str(v).strip()))
            except Exception:
                pass

    # ----- rpc plumbing -----
    def rpc_async(self, method: str, *args: Any) -> Any:
        return getattr(deluge_client.core, method)(*args)  # type: ignore

    def rpc(self, method: str, *args: Any, timeout: float = 8.0, quiet: bool = False) -> tuple[bool, Any]:
        """Run one RPC call while pumping the reactor. Returns (ok, value)."""
        st: dict[str, Any] = {"done": False, "ok": False, "v": None}
        try:
            d = self.rpc_async(method, *args)
        except Exception as e:
            if not quiet:
                self.err(f"{method}: {e}")
            return False, str(e)

        def good(v: Any) -> None:
            st.update(done=True, ok=True, v=v)

        def bad(f: Any) -> None:
            st.update(done=True, ok=False, v=format_failure(f))

        d.addCallbacks(good, bad)
        deadline = time.time() + timeout
        while not st["done"] and time.time() < deadline:
            reactor.iterate(0.02)  # type: ignore
        if not st["done"]:
            st.update(ok=False, v="timeout")
        if not st["ok"] and not quiet:
            self.err(f"{method}: {st['v']}")
        return st["ok"], st["v"]

    def _try(self, variants: list[tuple[str, tuple]]) -> bool:
        last = ""
        for name, args in variants:
            ok, v = self.rpc(name, *args, quiet=True, timeout=6)
            if ok:
                return True
            last = f"{name}: {v}"
        self.err(last)
        return False

    # ----- refresh -----
    def refresh(self) -> None:
        if self.mode == "rpc":
            if deluge_client and not deluge_client.connected():
                self.connected = False
                self.err("lost connection to deluged")
                return
            ok, status = self.rpc("get_torrents_status", {}, STATUS_KEYS, timeout=6)
            if ok:
                self.torrents = status or {}
        elif self.mode == "console":
            self._refresh_console()

    def _refresh_console(self) -> None:
        rc, out, err = run_console(["info"], timeout=15)
        torrents: dict[str, dict] = {}
        current: dict[str, Any] = {}
        tid = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Name:"):
                if tid and current:
                    torrents[tid] = current
                current = {"name": line[5:].strip(), "state": "?", "progress": 0.0,
                           "download_payload_rate": 0, "upload_payload_rate": 0,
                           "num_peers": 0, "num_seeds": 0, "total_size": 0,
                           "paused": False, "message": "", "time_added": 0}
                tid = current["name"]
            elif line.startswith("ID:") or line.startswith("Hash:"):
                tid = line.split(":", 1)[1].strip()
            elif line.startswith("State:"):
                st = line.split(":", 1)[1].strip()
                current["state"] = st.split()[0] if st else "?"
                current["paused"] = "Paused" in st
            elif line.startswith("Progress:"):
                try:
                    current["progress"] = float(line.split(":", 1)[1].strip().split("%")[0])
                except Exception:
                    pass
            elif line.startswith("Seeds:"):
                m = re.search(r"Seeds:\s*(\d+).*Peers:\s*(\d+)", line)
                if m:
                    current["num_seeds"], current["num_peers"] = int(m.group(1)), int(m.group(2))
        if tid and current:
            torrents[tid] = current
        self.torrents = torrents

    # ----- adding -----
    def _add_with_retry(self, method: str, first_arg: Any, extra_arg: Any, opts_extra: dict) -> tuple[bool, str]:
        args = [first_arg] if extra_arg is None else [first_arg, extra_arg]
        ok, v = self.rpc(method, *args, self.cfg.torrent_options(opts_extra))
        if not ok and re.search(r"option|unexpected|keyword|unknown|invalid", str(v), re.I) \
                and not re.search(r"already", str(v), re.I):
            log("retrying add with minimal options")
            ok, v = self.rpc(method, *args, self.cfg.minimal_options(opts_extra))
        if ok and v:
            return True, str(v)
        return False, str(v if not ok else "daemon returned nothing (duplicate?)")

    def add_torrent_bytes(self, name: str, data: bytes, prios: list[int] | None) -> tuple[bool, str]:
        log(f"ADD torrent file {name} prios={'yes' if prios else 'no'}")
        if self.mode == "rpc":
            extra = {"file_priorities": prios} if prios else {}
            return self._add_with_retry("add_torrent_file", name, base64.b64encode(data).decode("ascii"), extra)
        tmp = CONFIG_DIR / "tmp.torrent"
        tmp.write_bytes(data)
        return self._console_add(str(tmp))

    def add_magnet(self, magnet: str, prios: list[int] | None = None) -> tuple[bool, str]:
        magnet = magnet.strip()
        log(f"ADD magnet {magnet[:90]}")
        if self.mode == "rpc":
            ok, tid = self._add_with_retry("add_torrent_magnet", magnet, None, {})
            if ok and prios:
                # file priorities can only be set once the metadata is here; poll for it
                self.pending[tid] = {"prios": prios, "t0": time.monotonic(), "next": 0.0}
            return ok, tid
        return self._console_add(magnet)

    def add_url(self, url: str) -> tuple[bool, str]:
        if self.mode == "rpc":
            return self._add_with_retry("add_torrent_url", url, None, {})
        return self._console_add(url)

    def _console_add(self, target: str) -> tuple[bool, str]:
        rc, out, err = run_console(["add", "-p", str(self.cfg.download_dir), target], timeout=90)
        if rc == 0 and "could not connect" not in (out + err).lower():
            return True, out or "ok"
        return False, err or out or f"rc={rc}"

    def apply_pending(self) -> list[str]:
        """Apply file selections to magnets as soon as their metadata arrives."""
        notes: list[str] = []
        if self.mode != "rpc":
            return notes
        now = time.monotonic()
        for tid, p in list(self.pending.items()):
            if now < p["next"]:
                continue
            p["next"] = now + 0.4
            ok, st = self.rpc("get_torrent_status", tid, ["file_priorities"], quiet=True, timeout=3)
            cur = (st or {}).get("file_priorities") if ok and isinstance(st, dict) else None
            if cur is not None and len(cur) == len(p["prios"]):
                self.rpc("set_torrent_file_priorities", tid, p["prios"], quiet=True)
                del self.pending[tid]
                notes.append("File selection applied")
            elif now - p["t0"] > 150 or (now - p["t0"] > 8 and tid not in self.torrents):
                del self.pending[tid]
                notes.append("Could not apply the file selection yet - open the torrent and press f")
        return notes

    # ----- actions -----
    def pause(self, tid: str) -> None:
        if self.mode == "rpc":
            self._try([("pause_torrents", ([tid],)), ("pause_torrent", ([tid],)), ("pause_torrent", (tid,))])
        else:
            run_console(["pause", tid])

    def resume(self, tid: str) -> None:
        if self.mode == "rpc":
            self._try([("resume_torrents", ([tid],)), ("resume_torrent", ([tid],)), ("resume_torrent", (tid,))])
        else:
            run_console(["resume", tid])

    def pause_all(self) -> None:
        ids = list(self.torrents)
        if self.mode == "rpc":
            self._try([("pause_torrents", (ids,)), ("pause_torrent", (ids,)), ("pause_session", ())])
        else:
            run_console(["pause", "*"])

    def resume_all(self) -> None:
        ids = list(self.torrents)
        if self.mode == "rpc":
            self._try([("resume_torrents", (ids,)), ("resume_torrent", (ids,)), ("resume_session", ())])
        else:
            run_console(["resume", "*"])

    def remove(self, tid: str, remove_data: bool) -> None:
        if self.mode == "rpc":
            self._try([("remove_torrent", (tid, remove_data)), ("remove_torrents", ([tid], remove_data))])
        else:
            run_console(["rm"] + (["--remove_data"] if remove_data else []) + [tid])

    def set_priorities(self, tid: str, prios: list[int]) -> bool:
        if self.mode != "rpc":
            return False
        ok, _ = self.rpc("set_torrent_file_priorities", tid, prios)
        return ok

    def torrent_status(self, tid: str, keys: list[str]) -> dict:
        if self.mode == "rpc":
            ok, v = self.rpc("get_torrent_status", tid, keys)
            return v if ok and isinstance(v, dict) else {}
        return {}


# ---------------------------------------------------------------------------
# File picker (tree with tri-state checkboxes, like deluge-gtk)
# ---------------------------------------------------------------------------

VIDEO = {"mkv", "mp4", "avi", "mov", "wmv", "webm", "m4v", "ts", "mpg", "mpeg", "m2ts"}
AUDIO = {"mp3", "flac", "wav", "aac", "ogg", "opus", "m4a", "wma", "ape"}
ARCHIVE = {"zip", "rar", "7z", "tar", "gz", "xz", "bz2", "iso", "img", "bin"}
IMAGE = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "tif", "tiff"}
DOCS = {"pdf", "epub", "mobi", "txt", "nfo", "srt", "sub", "ass", "md", "cbz", "cbr", "sfv", "url"}


def ext_color(name: str) -> RGB:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in VIDEO:
        return T.accent2
    if ext in AUDIO:
        return T.up
    if ext in ARCHIVE:
        return T.warn
    if ext in IMAGE:
        return T.down
    if ext in DOCS:
        return T.text_dim
    return T.text


class FNode:
    __slots__ = ("name", "is_dir", "kids", "dirmap", "idx", "size", "leaves", "expanded", "depth", "parent")

    def __init__(self, name: str, is_dir: bool, depth: int, parent: "FNode | None" = None) -> None:
        self.name, self.is_dir, self.depth, self.parent = name, is_dir, depth, parent
        self.kids: list[FNode] = []
        self.dirmap: dict[str, FNode] = {}
        self.idx = -1
        self.size = 0
        self.leaves: list[int] = []
        self.expanded = True


class FilePicker:
    def __init__(self, name: str, files: list[dict], prios: list[int], progress: list[float] | None = None,
                 edit: bool = False, save_to: str = "") -> None:
        self.name, self.files, self.edit, self.save_to = name, files, edit, save_to
        n = len(files)
        prios = list(prios) + [4] * (n - len(prios))
        self.orig = [int(p) for p in prios[:n]]
        self.sel = [p > 0 and not files[i].get("hidden") for i, p in enumerate(self.orig)]
        self.progress = progress if progress and len(progress) == n else None
        self.n_visible = sum(1 for f in files if not f.get("hidden"))
        self.root = FNode("", True, -1)
        self.cursor = 0
        self.scroll = 0
        self._cache: dict[int, int] = {}
        self._tot: tuple[int, int, int, int] | None = None
        self._build()
        self.rows: list[FNode] = []
        self.rebuild()

    # -- tree
    def _build(self) -> None:
        for i, f in enumerate(self.files):
            if f.get("hidden"):
                continue
            parts = [p for p in str(f["path"]).split("/") if p] or [f"file_{i}"]
            node = self.root
            for d, part in enumerate(parts[:-1]):
                child = node.dirmap.get(part)
                if child is None:
                    child = FNode(part, True, d, node)
                    node.dirmap[part] = child
                    node.kids.append(child)
                node = child
            leaf = FNode(parts[-1], False, len(parts) - 1, node)
            leaf.idx, leaf.size = i, int(f.get("size") or 0)
            node.kids.append(leaf)
        big = self.n_visible > 80
        self._finish(self.root, big)

    def _finish(self, node: FNode, big: bool) -> None:
        node.expanded = (not big) or node.depth < 1
        for kid in node.kids:
            if kid.is_dir:
                self._finish(kid, big)
                node.leaves += kid.leaves
            else:
                node.leaves.append(kid.idx)
            node.size += kid.size
        node.kids.sort(key=lambda k: (not k.is_dir, natural_key(k.name)))

    def rebuild(self) -> None:
        rows: list[FNode] = []

        def walk(node: FNode) -> None:
            for kid in node.kids:
                rows.append(kid)
                if kid.is_dir and kid.expanded:
                    walk(kid)

        walk(self.root)
        self.rows = rows
        self.cursor = min(self.cursor, max(0, len(rows) - 1))

    # -- selection
    def _changed(self) -> None:
        self._cache.clear()
        self._tot = None

    def state(self, node: FNode) -> int:
        """0 none, 1 partial, 2 all"""
        if not node.is_dir:
            return 2 if self.sel[node.idx] else 0
        key = id(node)
        v = self._cache.get(key)
        if v is None:
            c = sum(1 for i in node.leaves if self.sel[i])
            v = 0 if c == 0 else (2 if c == len(node.leaves) else 1)
            self._cache[key] = v
        return v

    def toggle(self, node: FNode) -> None:
        if node.is_dir:
            want = self.state(node) != 2
            for i in node.leaves:
                self.sel[i] = want
        else:
            self.sel[node.idx] = not self.sel[node.idx]
        self._changed()

    def set_all(self, value: bool) -> None:
        for i, f in enumerate(self.files):
            if not f.get("hidden"):
                self.sel[i] = value
        self._changed()

    def invert(self) -> None:
        for i, f in enumerate(self.files):
            if not f.get("hidden"):
                self.sel[i] = not self.sel[i]
        self._changed()

    def totals(self) -> tuple[int, int, int, int]:
        if self._tot is None:
            ns = nt = ss = st = 0
            for i, f in enumerate(self.files):
                if f.get("hidden"):
                    continue
                sz = int(f.get("size") or 0)
                nt += 1
                st += sz
                if self.sel[i]:
                    ns += 1
                    ss += sz
            self._tot = (ns, nt, ss, st)
        return self._tot

    def result(self) -> list[int]:
        out = []
        for i, f in enumerate(self.files):
            if f.get("hidden"):
                out.append(self.orig[i])
            elif self.sel[i]:
                out.append(self.orig[i] if self.orig[i] > 0 else 4)
            else:
                out.append(0)
        return out

    # -- input
    def handle(self, k: str, page: int) -> str | None:
        n = len(self.rows)
        cur = self.rows[self.cursor] if n else None
        if k in ("esc", "q"):
            return "cancel"
        if k == "enter":
            return "confirm"
        if k in ("up", "k"):
            self.cursor = max(0, self.cursor - 1)
        elif k in ("down", "j"):
            self.cursor = min(max(0, n - 1), self.cursor + 1)
        elif k == "pgup":
            self.cursor = max(0, self.cursor - page)
        elif k == "pgdn":
            self.cursor = min(max(0, n - 1), self.cursor + page)
        elif k in ("home", "g"):
            self.cursor = 0
        elif k in ("end", "G"):
            self.cursor = max(0, n - 1)
        elif k == "space" and cur:
            self.toggle(cur)
        elif k in ("a", "A"):
            self.set_all(True)
        elif k in ("n", "N"):
            self.set_all(False)
        elif k in ("i", "I"):
            self.invert()
        elif k in ("right", "l") and cur:
            if cur.is_dir and not cur.expanded:
                cur.expanded = True
                self.rebuild()
            elif cur.is_dir and self.cursor + 1 < n:
                self.cursor += 1
        elif k in ("left", "h") and cur:
            if cur.is_dir and cur.expanded:
                cur.expanded = False
                self.rebuild()
            elif cur.parent is not None and cur.parent is not self.root:
                self.cursor = self.rows.index(cur.parent)
        elif k in ("e", "E"):
            open_all = k == "e"

            def setx(node: FNode) -> None:
                for kid in node.kids:
                    if kid.is_dir:
                        kid.expanded = open_all
                        setx(kid)

            setx(self.root)
            self.rebuild()
        return None

    # -- render
    def render(self, scr: Screen, anim_t: float) -> None:
        cols, rows = scr.cols, scr.rows
        w = min(cols - 2, 118)
        h = rows - 2
        left = (cols - w) // 2 + 1
        top = 2
        title = "Choose files to apply" if self.edit else "Choose files to download"
        draw_box(scr, top, left, w, h, title=title, right=f"{len(self.files) - (len(self.files) - self.n_visible)} files")
        iw = w - 2
        ix = left + 1
        ns, nt, ss, st = self.totals()
        scr.put(top + 1, ix, Line(iw).add(" ").add(fit(clean(self.name), iw - 2), T.text, bold=True).pad())
        # summary + gauge
        s = Line(iw).add(" ")
        col = T.warn if ns == 0 else T.ok
        s.add("◉ " if ns == nt else ("○ " if ns == 0 else "◐ "), col, bold=True)
        s.add(f"{ns}/{nt} files", T.text, bold=True).add("  ·  ", T.text_faint)
        s.add(f"{human_size(ss)}", T.accent2, bold=True).add(f" of {human_size(st)}", T.text_dim)
        if ns == 0:
            s.add("   nothing selected", T.warn)
        gw = min(24, max(0, iw - s.used - 10))
        if gw >= 8:
            s.add("   ")
            draw_bar(s, gw, (ss / st) if st else 0.0, T.accent, T.accent2, "block")
            s.add(f" {int(100 * ss / st) if st else 0:>3}%", T.text_dim)
        scr.put(top + 2, ix, s.pad())
        hsep(scr, top + 3, left, w)
        list_top = top + 4
        list_h = max(1, h - 7)
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        if self.cursor >= self.scroll + list_h:
            self.scroll = self.cursor - list_h + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.rows) - list_h)))
        size_w = 10
        for r in range(list_h):
            idx = self.scroll + r
            if idx >= len(self.rows):
                break
            node = self.rows[idx]
            cur = idx == self.cursor
            bgc = T.sel_bg if cur else None
            l = Line(iw, bgc)
            l.add("▌" if cur else " ", T.accent2)
            l.add("  " * max(0, node.depth))
            st_ = self.state(node)
            if node.is_dir:
                l.add("▾ " if node.expanded else "▸ ", T.accent)
            else:
                l.add("  ")
            l.add(("◉" if st_ == 2 else "◐" if st_ == 1 else "○") + " ",
                  T.ok if st_ == 2 else T.warn if st_ == 1 else T.text_faint, bold=True)
            right_w = size_w + (7 if self.progress else 0) + 2
            name_w = iw - l.used - right_w
            label = node.name + ("/" if node.is_dir else "")
            if node.is_dir:
                ncol, bold = (T.accent if st_ else T.text_dim), True
            else:
                ncol, bold = (ext_color(node.name) if st_ else T.text_faint), False
            l.add(fit(clean(label), name_w), ncol, bold=bold)
            l.fill_to(iw - right_w + 1)
            if self.progress and not node.is_dir:
                p = self.progress[node.idx] * 100
                l.add(f"{p:5.1f}% ", T.ok if p >= 99.95 else T.text_dim)
            elif self.progress:
                l.add("       ")
            l.add(f"{human_size(node.size):>{size_w}} ", T.text_dim if st_ else T.text_faint)
            scr.put(list_top + r, ix, l.pad())
        scrollbar(scr, left + w - 1, list_top, list_h, len(self.rows), list_h, self.scroll)
        hsep(scr, top + h - 3, left, w)
        foot = Line(iw).add(" ")
        if self.save_to:
            foot.add("save to ", T.text_faint).add(fit(self.save_to, iw - 12), T.text_dim)
        scr.put(top + h - 2, ix, foot.pad())


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

SORTS: dict[str, Callable[[dict], Any]] = {
    "added": lambda i: i.get("time_added") or 0,
    "name": lambda i: natural_key(str(i.get("name") or "")),
    "progress": lambda i: float(i.get("progress") or 0),
    "size": lambda i: i.get("total_size") or 0,
    "down": lambda i: i.get("download_payload_rate") or 0,
    "up": lambda i: i.get("upload_payload_rate") or 0,
    "state": lambda i: str(i.get("state") or ""),
}


class App:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.backend = Backend(cfg)
        self.net = NetState()
        self.events: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        self.reader = KeyReader()
        self.running = True
        self.mode = "main"  # main add remove files browse help log fetch
        self.sel = 0
        self.sel_tid: str | None = None
        self.scroll = 0
        self.sort_key = cfg.sort
        self.sort_desc = cfg.sort_desc
        self.show_details = cfg.details_open
        self.status = ""
        self.status_kind = "ok"
        self.status_until = 0.0
        self.input_buf = ""
        self.remove_tid = ""
        self.picker: FilePicker | None = None
        self.picker_cb: Callable[[list[int]], None] | None = None
        self.fetch: dict | None = None
        self.browse_path = cfg.browse_dir if cfg.browse_dir.is_dir() else HOME
        self.browse_entries: list[Path] = []
        self.browse_sel = 0
        self.log_lines: list[str] = []
        self.log_scroll = 0
        self.hist_down: deque[float] = deque(maxlen=200)
        self.hist_up: deque[float] = deque(maxlen=200)
        self.down_total = 0.0
        self.up_total = 0.0
        self.ks_tripped = False
        self.last_frame: str | None = None
        self.t_refresh = self.t_vpn = self.t_ip = self.t_ext = 0.0

    # ----- helpers -----
    def set_status(self, msg: str, kind: str = "ok", ttl: float = 4.0) -> None:
        self.status, self.status_kind = msg, kind
        self.status_until = time.monotonic() + ttl
        log(f"STATUS[{kind}]: {msg}")

    def run_bg(self, fn: Callable[[], Any], cb: Callable[[Any, Exception | None], None]) -> None:
        def work() -> None:
            try:
                r, e = fn(), None
            except Exception as ex:
                r, e = None, ex
            self.events.put((cb, r, e))

        threading.Thread(target=work, daemon=True).start()

    def drain_events(self) -> None:
        while True:
            try:
                cb, r, e = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                cb(r, e)
            except Exception:
                log_exc("event callback")

    def sorted_items(self) -> list[tuple[str, dict]]:
        key = SORTS.get(self.sort_key, SORTS["added"])
        items = sorted(self.backend.torrents.items(), key=lambda kv: key(kv[1]), reverse=self.sort_desc)
        if not items:
            self.sel, self.sel_tid = 0, None
            return items
        idx = next((i for i, (t, _) in enumerate(items) if t == self.sel_tid), None)
        if idx is None:
            idx = min(self.sel, len(items) - 1)
        self.sel, self.sel_tid = idx, items[idx][0]
        return items

    def move(self, delta: int) -> None:
        items = self.sorted_items()
        if not items:
            return
        self.sel = max(0, min(len(items) - 1, self.sel + delta))
        self.sel_tid = items[self.sel][0]

    def selected_tid(self) -> str | None:
        items = self.sorted_items()
        return items[self.sel][0] if items else None

    def vpn_blocked(self) -> bool:
        return self.cfg.kill_switch and not self.net.vpn

    # ----- periodic work -----
    def tick(self) -> None:
        now = time.monotonic()
        be = self.backend
        if now - self.t_refresh >= self.cfg.refresh:
            self.t_refresh = now
            if be.connected:
                be.refresh()
                if be.mode == "rpc":
                    self.down_total = sum(float(t.get("download_payload_rate") or 0) for t in be.torrents.values())
                    self.up_total = sum(float(t.get("upload_payload_rate") or 0) for t in be.torrents.values())
                    self.hist_down.append(self.down_total)
                    self.hist_up.append(self.up_total)
            else:
                be.maybe_reconnect()
        if be.pending:
            for note in be.apply_pending():
                self.set_status(note, "ok" if note.startswith("File") else "warn", 6)
        if now - self.t_vpn >= 2.0:
            self.t_vpn = now
            self.check_vpn()
        interval = self.cfg.ip_interval
        if not self.net.last and not self.net.busy and self.t_ip == 0.0:
            self.t_ip = now
            self.check_ip()
        elif interval and now - self.t_ip >= interval:
            self.t_ip = now
            self.check_ip()
        if now - self.t_ext >= 15 and be.mode == "rpc" and be.connected:
            self.t_ext = now
            be.update_external_ip()
        self.net.ext_ip, self.net.bound, self.net.listen = be.ext_ip, be.bound, be.listen
        if self.mode == "fetch":
            self.tick_fetch()

    def check_vpn(self) -> None:
        vpn = detect_vpn(self.cfg.vpn_prefixes)
        changed = vpn != self.net.vpn
        self.net.vpn = vpn
        if changed and self.net.last:
            self.t_ip = time.monotonic()
            self.check_ip()
        if self.cfg.kill_switch:
            if not vpn and not self.ks_tripped:
                self.ks_tripped = True
                if self.backend.connected:
                    self.backend.pause_all()
                self.set_status("Kill switch: no VPN detected - everything paused", "err", 12)
            elif vpn and self.ks_tripped:
                self.ks_tripped = False
                if self.cfg.kill_resume and self.backend.connected:
                    self.backend.resume_all()
                    self.set_status(f"VPN ({vpn}) is back - resumed", "ok", 8)
                else:
                    self.set_status(f"VPN ({vpn}) is back - press R to resume", "ok", 10)

    def check_ip(self) -> None:
        if self.net.busy:
            return
        self.net.busy = True
        prev = {"ip": self.net.ip, "place": self.net.place, "org": self.net.org}
        services, geo = list(self.cfg.ip_services), self.cfg.geo
        self.run_bg(lambda: lookup_public_ip(services, geo, prev), self._on_ip)

    def _on_ip(self, res: dict | None, exc: Exception | None) -> None:
        n = self.net
        n.busy = False
        n.last = time.monotonic()
        if exc or not res:
            n.err = str(exc or "lookup failed")
            log(f"ip lookup error: {n.err}")
        elif res["ip"]:
            n.ip, n.place, n.org, n.err = res["ip"], res["place"], res["org"], ""
        else:
            n.err = res["err"] or "lookup failed"
            log(f"ip lookup failed: {n.err}")

    # ----- adding torrents -----
    def begin_add(self, text: str) -> None:
        text = text.strip().strip("'\"")
        if not text:
            return
        if self.vpn_blocked():
            self.set_status("Kill switch: connect your VPN before adding torrents", "err", 6)
            return
        if not self.backend.connected:
            self.set_status("Not connected to deluged", "err", 5)
            return
        low = text.lower()
        if low.startswith("magnet:"):
            self.begin_magnet(text)
        elif low.startswith(("http://", "https://")):
            self.set_status("Downloading .torrent…", "info", 20)
            self.run_bg(lambda: http_get(text, timeout=12, limit=8_000_000), lambda r, e: self._on_url(text, r, e))
        else:
            if low.startswith("file://"):
                text = text[7:]
            p = Path(text).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p) if (Path.cwd() / p).exists() else (HOME / p)
            self.begin_file(p)

    def _on_url(self, url: str, data: bytes | None, exc: Exception | None) -> None:
        if exc or not data or not data.startswith(b"d"):
            log(f"url fetch failed ({exc}); letting deluged handle it")
            ok, msg = self.backend.add_url(url)
            self.set_status("Added" if ok else f"Add failed: {msg}", "ok" if ok else "err", 6)
            return
        name = Path(url.split("?")[0]).name or "download.torrent"
        self.after_torrent_bytes(name, data)

    def begin_file(self, path: Path) -> None:
        if not path.is_file() or path.suffix.lower() != ".torrent":
            self.set_status(f"Not a .torrent file: {path.name}", "err", 6)
            return
        try:
            data = path.read_bytes()
        except Exception as e:
            self.set_status(f"Cannot read file: {e}", "err", 6)
            return
        self.after_torrent_bytes(path.name, data)

    def wants_picker(self, n_files: int) -> bool:
        mode = self.cfg.file_picker
        if mode == "never" or self.backend.mode != "rpc":
            return False
        return n_files > 1 or mode == "always"

    def after_torrent_bytes(self, name: str, data: bytes) -> None:
        parsed = parse_torrent_bytes(data)
        if parsed and self.wants_picker(len([f for f in parsed[1] if not f["hidden"]])):
            tname, files = parsed
            prios = [0 if f["hidden"] else 4 for f in files]
            self.open_picker(FilePicker(tname, files, prios, save_to=str(self.cfg.download_dir)),
                             lambda pr: self.finish_torrent(name, data, pr))
            return
        self.finish_torrent(name, data, None)

    def finish_torrent(self, name: str, data: bytes, prios: list[int] | None) -> None:
        ok, msg = self.backend.add_torrent_bytes(name, data, prios)
        self.set_status("Torrent added" if ok else f"Add failed: {msg}", "ok" if ok else "err", 6)
        self.backend.refresh()

    def begin_magnet(self, magnet: str) -> None:
        if self.cfg.file_picker == "never" or self.backend.mode != "rpc":
            self.finish_magnet(magnet, None)
            return
        f: dict = {"magnet": magnet, "t0": time.monotonic(), "done": False, "res": None, "err": ""}
        self.fetch = f
        self.mode = "fetch"
        try:
            d = self.backend.rpc_async("prefetch_magnet_metadata", magnet, self.cfg.magnet_timeout)

            def ok(res: Any) -> None:
                f["res"], f["done"] = res, True

            def bad(fail: Any) -> None:
                f["err"], f["done"] = format_failure(fail), True

            d.addCallbacks(ok, bad)
        except Exception as e:
            f["err"], f["done"] = str(e), True

    def tick_fetch(self) -> None:
        f = self.fetch
        if not f:
            self.mode = "main"
            return
        if not f["done"] and time.monotonic() - f["t0"] > self.cfg.magnet_timeout + 6:
            f["err"], f["done"] = "timed out", True
        if not f["done"]:
            return
        self.fetch = None
        self.mode = "main"
        files = None
        if not f["err"]:
            files = self._files_from_prefetch(f["res"])
        if not files:
            if f["err"] or True:
                log(f"magnet prefetch: no file list ({f['err'] or 'unparsable'})")
            self.set_status("No file list from peers - added everything (press f later to choose)", "warn", 8)
            self.finish_magnet(f["magnet"], None)
            return
        name, flist = files
        if not self.wants_picker(len([x for x in flist if not x["hidden"]])):
            self.finish_magnet(f["magnet"], None)
            return
        prios = [0 if x["hidden"] else 4 for x in flist]
        self.open_picker(FilePicker(name, flist, prios, save_to=str(self.cfg.download_dir)),
                         lambda pr: self.finish_magnet(f["magnet"], pr))

    @staticmethod
    def _files_from_prefetch(res: Any) -> tuple[str, list[dict]] | None:
        cands = res if isinstance(res, (list, tuple)) else [res]
        for c in cands:
            if c is None or not isinstance(c, (str, bytes)) or len(c) < 40:
                continue
            try:
                raw = base64.b64decode(c)
            except Exception:
                raw = c if isinstance(c, bytes) else c.encode("latin-1", "ignore")
            parsed = parse_torrent_bytes(raw)
            if parsed:
                return parsed
        return None

    def finish_magnet(self, magnet: str, prios: list[int] | None) -> None:
        ok, msg = self.backend.add_magnet(magnet, prios)
        self.set_status("Magnet added" if ok else f"Add failed: {msg}", "ok" if ok else "err", 6)
        self.backend.refresh()

    def open_picker(self, picker: FilePicker, cb: Callable[[list[int]], None]) -> None:
        self.picker, self.picker_cb = picker, cb
        self.mode = "files"

    def open_file_editor(self, tid: str) -> None:
        st = self.backend.torrent_status(tid, ["files", "file_priorities", "name", "file_progress"])
        files = st.get("files") or []
        if not files:
            self.set_status("No file list yet - waiting for metadata", "warn", 5)
            return
        flist = [{"path": f.get("path", f"file_{i}"), "size": f.get("size", 0),
                  "hidden": is_pad(str(f.get("path", "")))} for i, f in enumerate(files)]
        prios = list(st.get("file_priorities") or [4] * len(files))
        if len(flist) < 2 and self.cfg.file_picker != "always":
            self.set_status("This torrent has a single file", "info", 4)
            return
        info = self.backend.torrents.get(tid, {})
        self.open_picker(
            FilePicker(clean(st.get("name") or tid), flist, prios, st.get("file_progress"), edit=True,
                       save_to=str(info.get("save_path") or "")),
            lambda pr: self._apply_priorities(tid, pr),
        )

    def _apply_priorities(self, tid: str, prios: list[int]) -> None:
        ok = self.backend.set_priorities(tid, prios)
        self.set_status("File selection applied" if ok else "Could not apply file selection",
                        "ok" if ok else "err", 5)
        self.backend.refresh()

    # ----- input -----
    def handle(self, k: str) -> None:
        if k == "ctrl+l":
            self.last_frame = None
            return
        if k == "ctrl+c":
            self.running = False
            return
        m = self.mode
        if m == "main":
            self._handle_main(k)
        elif m == "add":
            self._handle_add(k)
        elif m == "remove":
            self._handle_remove(k)
        elif m == "files":
            self._handle_files(k)
        elif m == "browse":
            self._handle_browse(k)
        elif m == "help":
            self.mode = "main"
        elif m == "log":
            self._handle_log(k)
        elif m == "fetch":
            if k == "esc":
                if self.fetch:
                    self.fetch["done"] = True
                    self.fetch["err"] = "cancelled"
                    self.fetch["magnet"] = ""
                self.fetch = None
                self.mode = "main"
                self.set_status("Cancelled", "info")
            elif k == "enter" and self.fetch:
                mg = self.fetch["magnet"]
                self.fetch = None
                self.mode = "main"
                self.finish_magnet(mg, None)

    def _handle_main(self, k: str) -> None:
        be = self.backend
        page = max(1, self.rows_visible())
        if k in ("up", "k"):
            self.move(-1)
        elif k in ("down", "j"):
            self.move(1)
        elif k == "pgup":
            self.move(-page)
        elif k == "pgdn":
            self.move(page)
        elif k in ("home", "g"):
            self.move(-10**6)
        elif k in ("end", "G"):
            self.move(10**6)
        elif k in ("q", "Q"):
            self.running = False
        elif k in ("h", "H", "?"):
            self.mode = "help"
        elif k in ("a", "A"):
            self.input_buf = ""
            self.mode = "add"
        elif k in ("t", "T"):
            self.browse_path = self.cfg.browse_dir if self.cfg.browse_dir.is_dir() else HOME
            self._load_browse()
            self.mode = "browse"
        elif k in ("space", "p", "r"):
            tid = self.selected_tid()
            if not tid:
                return
            info = be.torrents.get(tid, {})
            paused = bool(info.get("paused")) or info.get("state") == "Paused"
            want_pause = (k == "p") or (k == "space" and not paused)
            if not want_pause and self.vpn_blocked():
                self.set_status("Kill switch: no VPN detected", "err", 5)
                return
            (be.pause if want_pause else be.resume)(tid)
            self.set_status("Paused" if want_pause else "Resumed", "info")
            be.refresh()
        elif k == "P":
            be.pause_all()
            self.set_status("Paused everything", "info")
            be.refresh()
        elif k == "R":
            if self.vpn_blocked():
                self.set_status("Kill switch: no VPN detected", "err", 5)
                return
            be.resume_all()
            self.set_status("Resumed everything", "info")
            be.refresh()
        elif k in ("delete", "x", "X"):
            tid = self.selected_tid()
            if tid:
                self.remove_tid = tid
                self.mode = "remove"
        elif k in ("f", "F"):
            tid = self.selected_tid()
            if tid:
                self.open_file_editor(tid)
            else:
                self.set_status("No torrent selected", "warn")
        elif k == "enter":
            self.show_details = not self.show_details
        elif k == "s":
            i = SORT_FIELDS.index(self.sort_key) if self.sort_key in SORT_FIELDS else 0
            self.sort_key = SORT_FIELDS[(i + 1) % len(SORT_FIELDS)]
            self.set_status(f"Sorted by {self.sort_key}", "info", 2)
        elif k == "S":
            self.sort_desc = not self.sort_desc
            self.set_status("Descending" if self.sort_desc else "Ascending", "info", 2)
        elif k in ("i", "I"):
            self.t_ip = time.monotonic()
            self.check_ip()
            self.check_vpn()
            self.backend.read_bind_info()
            self.backend.update_external_ip()
            self.set_status("Re-checking IP…", "info", 2)
        elif k in ("c", "C"):
            self.reload_config()
        elif k in ("l", "L"):
            self.log_lines = read_log_tail(400)
            self.log_scroll = 0
            self.mode = "log"

    def reload_config(self) -> None:
        self.cfg.load()
        self.sort_key, self.sort_desc = self.cfg.sort, self.cfg.sort_desc
        self.show_details = self.cfg.details_open
        try:
            self.cfg.download_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.cfg.warnings.append(f"download_dir: {e}")
        if self.backend.mode == "rpc":
            self.backend.apply_session_config()
            self.backend.read_bind_info()
        self.t_vpn = 0.0
        self.last_frame = None
        if self.cfg.warnings:
            self.set_status("config.ini: " + self.cfg.warnings[0], "warn", 9)
        else:
            self.set_status("Config reloaded", "ok")

    def _handle_add(self, k: str) -> None:
        if k == "esc":
            self.mode = "main"
        elif k == "backspace":
            self.input_buf = self.input_buf[:-1]
        elif k == "ctrl+u":
            self.input_buf = ""
        elif k == "ctrl+w":
            self.input_buf = re.sub(r"\S*\s*$", "", self.input_buf)
        elif k == "enter":
            text = self.input_buf.strip()
            self.mode = "main"
            if text:
                self.begin_add(text)
        elif k.startswith("paste:"):
            self.input_buf += re.sub(r"[\r\n\t]+", "", k[6:])
        elif k == "space":
            self.input_buf += " "
        elif len(k) == 1 and k.isprintable():
            self.input_buf += k

    def _handle_remove(self, k: str) -> None:
        if k in ("esc", "backspace", "n"):
            self.mode = "main"
        elif k in ("1", "2"):
            self.backend.remove(self.remove_tid, k == "2")
            self.set_status("Removed + deleted data" if k == "2" else "Removed (files kept)", "info")
            self.mode = "main"
            self.backend.refresh()

    def _handle_files(self, k: str) -> None:
        p = self.picker
        if not p:
            self.mode = "main"
            return
        act = p.handle(k, max(1, term_size()[1] - 9))
        if act == "cancel":
            self.picker, self.picker_cb, self.mode = None, None, "main"
            self.set_status("Cancelled", "info", 2)
        elif act == "confirm":
            if p.totals()[0] == 0:
                self.set_status("Select at least one file", "warn", 3)
                return
            cb, res = self.picker_cb, p.result()
            self.picker, self.picker_cb, self.mode = None, None, "main"
            if cb:
                cb(res)

    def _handle_browse(self, k: str) -> None:
        n = len(self.browse_entries)
        if k in ("esc", "q"):
            self.mode = "main"
        elif k in ("up", "k"):
            self.browse_sel = max(0, self.browse_sel - 1)
        elif k in ("down", "j"):
            self.browse_sel = min(max(0, n - 1), self.browse_sel + 1)
        elif k == "pgup":
            self.browse_sel = max(0, self.browse_sel - 10)
        elif k == "pgdn":
            self.browse_sel = min(max(0, n - 1), self.browse_sel + 10)
        elif k in ("home", "g"):
            self.browse_sel = 0
        elif k in ("end", "G"):
            self.browse_sel = max(0, n - 1)
        elif k in ("backspace", "left", "h"):
            parent = self.browse_path.parent
            if parent != self.browse_path:
                self.browse_path = parent
                self._load_browse()
        elif k in ("enter", "right", "l"):
            if not self.browse_entries:
                return
            p = self.browse_entries[self.browse_sel]
            if p.is_dir():
                self.browse_path = p
                self._load_browse()
            elif p.suffix.lower() == ".torrent" and k == "enter":
                self.mode = "main"
                if self.vpn_blocked():
                    self.set_status("Kill switch: connect your VPN before adding torrents", "err", 6)
                else:
                    self.begin_file(p)

    def _handle_log(self, k: str) -> None:
        if k == "up":
            self.log_scroll = min(len(self.log_lines), self.log_scroll + 1)
        elif k == "down":
            self.log_scroll = max(0, self.log_scroll - 1)
        elif k == "pgup":
            self.log_scroll = min(len(self.log_lines), self.log_scroll + 15)
        elif k == "pgdn":
            self.log_scroll = max(0, self.log_scroll - 15)
        else:
            self.mode = "main"

    def _load_browse(self) -> None:
        try:
            entries = []
            for p in sorted(self.browse_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if p.name.startswith("."):
                    continue
                if p.is_dir() or p.suffix.lower() == ".torrent":
                    entries.append(p)
            self.browse_entries = entries
            self.browse_sel = 0
        except Exception as e:
            self.set_status(f"Browse error: {e}", "err")
            self.browse_entries = []

    # ----- drawing -----
    def rows_visible(self) -> int:
        cols, rows = term_size()
        inner_h = rows - 5
        detail = 6 if self.show_details and inner_h >= 14 else 0
        list_h = inner_h - detail
        if self.cfg.layout == "card":
            return max(1, (list_h + self.cfg.spacing) // (2 + self.cfg.spacing))
        return max(1, list_h)

    def anim(self) -> float:
        return time.monotonic() if self.cfg.animations else 0.0

    def spinner(self) -> str:
        return SPINNER[int(self.anim() * 10) % len(SPINNER)] if self.cfg.animations else "◌"

    def render(self) -> str:
        cols, rows = term_size()
        scr = Screen(cols, rows)
        if cols < 56 or rows < 14:
            scr.put(1, 1, Line(cols).add(" Terminal too small", T.warn, bold=True))
            scr.put(2, 1, Line(cols).add(f" need 56x14, have {cols}x{rows}", T.text_dim))
            return "".join(scr.out)
        self.draw_main(scr)
        m = self.mode
        if m == "add":
            self.draw_add(scr)
        elif m == "remove":
            self.draw_remove(scr)
        elif m == "files" and self.picker:
            self.picker.render(scr, self.anim())
        elif m == "browse":
            self.draw_browse(scr)
        elif m == "help":
            self.draw_help(scr)
        elif m == "log":
            self.draw_log(scr)
        elif m == "fetch":
            self.draw_fetch(scr)
        scr.put(rows, 1, self.footer(cols))
        return "".join(scr.out)

    def draw(self) -> None:
        frame = self.render()
        if frame != self.last_frame:
            self.last_frame = frame
            sys.stdout.write(SYNC_ON + CLEAR + frame + SYNC_OFF)
            sys.stdout.flush()

    def footer(self, cols: int) -> Line:
        right = [("h", "Help"), ("q", "Quit")]
        m = self.mode
        if m == "main":
            return hint_line([("a", "Add"), ("t", "Browse"), ("␣", "Pause/Resume"), ("f", "Files"),
                              ("⏎", "Details"), ("x", "Remove"), ("s", "Sort"), ("i", "IP"), ("c", "Config")],
                             cols, right)
        table = {
            "add": [("⏎", "Add"), ("^U", "Clear"), ("esc", "Cancel")],
            "remove": [("1", "Keep files"), ("2", "Delete data"), ("esc", "Cancel")],
            "files": [("␣", "Toggle"), ("a", "All"), ("n", "None"), ("i", "Invert"), ("←→", "Fold"),
                      ("e/E", "Expand/Collapse all"), ("⏎", "Apply" if self.picker and self.picker.edit else "Download"),
                      ("esc", "Cancel")],
            "browse": [("⏎", "Open / add"), ("⌫", "Parent"), ("esc", "Cancel")],
            "help": [("any key", "Close")],
            "log": [("↑↓", "Scroll"), ("any other key", "Close")],
            "fetch": [("⏎", "Skip & add everything"), ("esc", "Cancel")],
        }
        return hint_line(table.get(m, []), cols)

    def draw_header(self, scr: Screen) -> None:
        cols = scr.cols
        be, net, cfg = self.backend, self.net, self.cfg
        l = Line(cols).add(" ◆ ", T.accent2, bold=True).grad("TDeluge", T.accent, T.accent2, bold=True)
        l.add(f" {VERSION}", T.text_faint).add("  ")
        if be.connected:
            l.add(f" ● {be.mode} ", T.pill_fg, bold=True, bg=T.ok)
        else:
            l.add(" ○ offline ", T.pill_fg, bold=True, bg=T.err)
        if self.ks_tripped:
            l.add(" ").add(" KILL SWITCH ", T.pill_fg, bold=True, bg=T.err)
        gw = cfg.graph_width

        def make_right(graphs: bool) -> Line:
            r = Line(260).add("▼ ", T.down, bold=True).add(f"{human_rate(self.down_total):>10}", T.text, bold=True)
            if graphs:
                r.add(" ").spark(self.hist_down, gw, T.text_faint, T.down)
            r.add("   ").add("▲ ", T.up, bold=True).add(f"{human_rate(self.up_total):>10}", T.text, bold=True)
            if graphs:
                r.add(" ").spark(self.hist_up, gw, T.text_faint, T.up)
            return r.add(" ")

        if be.connected and be.mode == "rpc":
            for graphs in ((True, False) if cfg.graphs else (False,)):
                r = make_right(graphs)
                if l.used + r.used + 1 <= cols:
                    scr.put(1, cols - r.used + 1, r)
                    break
        scr.put(1, 1, l)

        # row 2: VPN / IP / what deluge sees
        l2 = Line(cols).add(" ")

        def chunk(build: Callable[[Line], None]) -> None:
            c = Line(160)
            build(c)
            c.add("  ")
            if l2.used + c.used <= cols:
                l2.extend(c)

        if net.vpn:
            chunk(lambda c: c.add(f" ● VPN {net.vpn} ", T.pill_fg, bold=True, bg=T.ok))
        else:
            chunk(lambda c: c.add(" ○ NO VPN ", T.pill_fg, bold=True, bg=T.err if cfg.kill_switch else T.warn))

        def ip_chunk(c: Line) -> None:
            c.add("IP ", T.text_dim)
            if net.ip:
                shown = redact(net.ip) if cfg.redact_ip else net.ip
                c.add(shown, T.ok if net.vpn else T.text, bold=True)
                if net.err:
                    c.add(" (stale)", T.warn)
            elif net.err:
                c.add("lookup failed", T.err, bold=True).add(" - i to retry, l for log", T.text_faint)
            else:
                c.add(f"{self.spinner()} resolving…", T.text_dim)

        chunk(ip_chunk)
        if net.ip and (net.place or net.org):
            org = re.sub(r"^AS\d+\s+", "", net.org)
            txt = " · ".join(x for x in (net.place, fit(org, 26)) if x)
            chunk(lambda c: c.add(txt, T.text_dim))
        if net.ext_ip:
            same = net.ext_ip == net.ip
            def dchunk(c: Line) -> None:
                c.add("Deluge ", T.text_dim)
                c.add(redact(net.ext_ip) if cfg.redact_ip else net.ext_ip, T.text, bold=True)
                c.add(" ✓" if same else " ≠", T.ok if same else T.warn, bold=True)
            chunk(dchunk)
        if net.bound:
            chunk(lambda c: c.add("⇄ bound ", T.text_dim).add(net.bound, T.accent, bold=True))
        scr.put(2, 1, l2)

    # --- torrent list
    def state_style(self, info: dict) -> tuple[str, RGB, str]:
        st = str(info.get("state") or "?")
        prog = float(info.get("progress") or 0)
        if info.get("paused") or st == "Paused":
            return ("Done", T.text_dim, "✓") if prog >= 100 else ("Paused", T.text_dim, "‖")
        if st == "Error":
            return "Error", T.err, "✗"
        if st == "Queued":
            return "Queued", T.text_dim, "◌"
        if st in ("Checking", "Allocating", "Moving"):
            return st, T.warn, self.spinner()
        if st == "Seeding":
            return "Seeding", T.ok, "▲"
        if st == "Downloading":
            if not (info.get("total_size") or 0):
                return "Metadata", T.accent, self.spinner()
            return "Downloading", T.down, "▼"
        return st, T.text, "•"

    def bar_colors(self, label: str) -> tuple[RGB, RGB]:
        if label in ("Seeding", "Done"):
            return T.ok, lerp(T.ok, (255, 255, 255), 0.35)
        if label == "Error":
            return T.err, T.err
        if label in ("Paused", "Queued"):
            return T.text_faint, T.text_dim
        if label in ("Checking", "Allocating", "Moving"):
            return T.warn, lerp(T.warn, (255, 255, 255), 0.3)
        return T.accent, T.accent2

    def card_lines(self, tid: str, info: dict, iw: int, selected: bool) -> list[Line]:
        bgc = T.sel_bg if selected else None
        label, color, icon = self.state_style(info)
        name = clean(info.get("name") or tid)
        total = info.get("total_size") or 0
        size = human_size(total) if total else "—"
        prog = float(info.get("progress") or 0)
        l1 = Line(iw, bgc).add("▌" if selected else " ", T.accent2).add(" ").add(icon, color, bold=True).add("  ")
        right = f"{label:<11} {size:>9} "
        l1.add(fit(name, iw - l1.used - cw(right) - 1), T.sel_fg if selected else T.text, bold=True)
        l1.fill_to(iw - cw(right)).add(f"{label:<11} ", color, bold=True).add(f"{size:>9} ", T.text_dim)

        down = float(info.get("download_payload_rate") or 0)
        up = float(info.get("upload_payload_rate") or 0)
        stats: list[tuple[str, RGB]] = []
        if label == "Error" and info.get("message"):
            stats.append((clean(info["message"]), T.err))
        else:
            stats.append((f"▼ {human_rate(down)}", T.down if down else T.text_faint))
            stats.append((f"▲ {human_rate(up)}", T.up if up else T.text_faint))
            if label == "Downloading":
                stats.append((f"eta {human_eta(info.get('eta'))}", T.text_dim))
            stats.append((f"{info.get('num_peers') or 0} peers · {info.get('num_seeds') or 0} seeds", T.text_dim))
            ratio = info.get("ratio")
            if isinstance(ratio, (int, float)) and ratio >= 0:
                stats.append((f"ratio {ratio:.2f}", T.text_dim))
        pct = f"{prog:5.1f}%"
        avail = iw - 5 - 1 - cw(pct) - 1
        used, chosen = 0, []
        for text, col in stats:
            need = cw(text) + 2
            if used + need + 12 <= avail:
                chosen.append((text, col))
                used += need
            else:
                break
        bar_w = max(6, avail - used)
        l2 = Line(iw, bgc).add(" " * 5)
        c1, c2 = self.bar_colors(label)
        draw_bar(l2, bar_w, prog / 100.0, c1, c2, self.cfg.bar_style)
        l2.add(" ").add(pct, T.sel_fg if selected else T.text, bold=True)
        for text, col in chosen:
            l2.add("  ").add(text, col)
        return [l1.pad(), l2.pad()]

    def compact_line(self, tid: str, info: dict, iw: int, selected: bool) -> Line:
        bgc = T.sel_bg if selected else None
        label, color, icon = self.state_style(info)
        prog = float(info.get("progress") or 0)
        down = float(info.get("download_payload_rate") or 0)
        up = float(info.get("upload_payload_rate") or 0)
        l = Line(iw, bgc).add("▌" if selected else " ", T.accent2).add(" ").add(icon, color, bold=True).add(" ")
        parts = [f"{prog:5.1f}%", f"▼ {human_rate(down):>11}", f"▲ {human_rate(up):>11}"]
        right_w = 14 + 1 + sum(cw(p) + 1 for p in parts)
        if iw - l.used - right_w < 14:
            parts = parts[:1]
            right_w = 14 + 1 + sum(cw(p) + 1 for p in parts)
        l.add(fit(clean(info.get("name") or tid), iw - l.used - right_w - 1), T.sel_fg if selected else T.text, bold=selected)
        l.fill_to(iw - right_w)
        c1, c2 = self.bar_colors(label)
        draw_bar(l, 14, prog / 100.0, c1, c2, self.cfg.bar_style)
        l.add(" ").add(parts[0], T.text, bold=True)
        if len(parts) > 1:
            l.add(" ").add(parts[1], T.down if down else T.text_faint).add(" ").add(parts[2], T.up if up else T.text_faint)
        return l.pad()

    def draw_main(self, scr: Screen) -> None:
        cols, rows = scr.cols, scr.rows
        self.draw_header(scr)
        items = self.sorted_items()
        n = len(items)
        top, left, w, h = 3, 1, cols, rows - 3
        arrow = "▼" if self.sort_desc else "▲"
        draw_box(scr, top, left, w, h, title=f"Torrents · {n}", right=f"sort: {self.sort_key} {arrow}")
        iw, ix = w - 2, left + 1
        inner_top, inner_bot = top + 1, top + h - 2
        inner_h = inner_bot - inner_top + 1
        detail_h = 6 if self.show_details and n and inner_h >= 14 else 0
        list_h = inner_h - detail_h
        if n == 0:
            self.draw_empty(scr, inner_top, ix, iw, list_h)
        else:
            card = self.cfg.layout == "card"
            stride = (2 + self.cfg.spacing) if card else 1
            per = max(1, (list_h + (self.cfg.spacing if card else 0)) // stride)
            if self.sel < self.scroll:
                self.scroll = self.sel
            if self.sel >= self.scroll + per:
                self.scroll = self.sel - per + 1
            self.scroll = max(0, min(self.scroll, max(0, n - per)))
            for i in range(per):
                idx = self.scroll + i
                if idx >= n:
                    break
                tid, info = items[idx]
                row = inner_top + i * stride
                if card:
                    for j, ln in enumerate(self.card_lines(tid, info, iw, idx == self.sel)):
                        scr.put(row + j, ix, ln)
                else:
                    scr.put(row, ix, self.compact_line(tid, info, iw, idx == self.sel))
            scrollbar(scr, left + w - 1, inner_top, list_h, n, per, self.scroll)
        if detail_h and n:
            hsep(scr, inner_top + list_h, left, w, "Details")
            self.draw_details(scr, inner_top + list_h + 1, ix, iw, items[self.sel])
        if time.monotonic() < self.status_until and self.status:
            col = {"ok": T.ok, "err": T.err, "warn": T.warn, "info": T.accent}.get(self.status_kind, T.text)
            icon = {"ok": "✓", "err": "✗", "warn": "!", "info": "•"}.get(self.status_kind, "•")
            msg = Line(w - 6).add(f" {icon} ", col, bold=True).add(fit(clean(self.status), w - 12), T.text, bold=True).add(" ")
            scr.put(rows - 1, 3, msg)

    def draw_empty(self, scr: Screen, top: int, ix: int, iw: int, h: int) -> None:
        if not self.backend.connected:
            lines = [("○", T.err, True), ("Not connected to deluged", T.err, True),
                     (fit(clean(self.backend.last_error or "start it with:  deluged"), iw - 4), T.text_dim, False),
                     ("retrying every few seconds…", T.text_faint, False)]
        else:
            lines = [("⇣", T.accent, True), ("No torrents yet", T.text, True), ("", T.text, False),
                     ("press  a  to paste a magnet link, URL or path", T.text_dim, False),
                     ("press  t  to browse for a .torrent file", T.text_dim, False)]
        start = top + max(0, (h - len(lines)) // 2)
        for i, (txt, col, bold) in enumerate(lines):
            pad = max(0, (iw - cw(txt)) // 2)
            scr.put(start + i, ix, Line(iw).add(" " * pad).add(txt, col, bold=bold))

    def draw_details(self, scr: Screen, top: int, ix: int, iw: int, item: tuple[str, dict]) -> None:
        tid, info = item

        def kv(*pairs: tuple[str, str]) -> Line:
            l = Line(iw).add(" ")
            for k, v in pairs:
                l.add(f"{k} ", T.text_faint).add(v, T.text).add("   ")
            return l

        def ratio_s() -> str:
            r = info.get("ratio")
            return f"{r:.2f}" if isinstance(r, (int, float)) and r >= 0 else "—"

        added = info.get("time_added")
        added_s = datetime.fromtimestamp(added).strftime("%Y-%m-%d %H:%M") if added else "—"
        scr.put(top, ix, Line(iw).add(" ").add(fit(clean(info.get("name") or tid), iw - 2), T.accent, bold=True))
        scr.put(top + 1, ix, kv(("Path", fit(clean(info.get("save_path") or "—"), iw - 8))))
        scr.put(top + 2, ix, kv(
            ("Done", f"{human_size(info.get('total_done') or 0)} of {human_size(info.get('total_wanted') or info.get('total_size') or 0)}"),
            ("Uploaded", human_size(info.get("total_uploaded") or 0)), ("Ratio", ratio_s()),
            ("ETA", human_eta(info.get("eta")))))
        scr.put(top + 3, ix, kv(
            ("Peers", f"{info.get('num_peers') or 0}/{info.get('total_peers') or 0}"),
            ("Seeds", f"{info.get('num_seeds') or 0}/{info.get('total_seeds') or 0}"),
            ("Files", str(info.get("num_files") or "—")), ("Added", added_s)))
        trk = clean(info.get("tracker_host") or "—")
        tst = clean(info.get("tracker_status") or "")
        msg = clean(info.get("message") or "")
        scr.put(top + 4, ix, kv(("Tracker", fit(f"{trk}  {tst}".strip(), iw // 2)), ("ID", tid[:16] + ("…" if len(tid) > 16 else "")),
                                *([("Msg", fit(msg, 40))] if msg and msg != "OK" else [])))

    # --- modals
    def modal(self, scr: Screen, w: int, h: int, title: str, right: str = "") -> tuple[int, int, int, int]:
        w = min(w, scr.cols - 2)
        h = min(h, scr.rows - 2)
        top = max(2, (scr.rows - h) // 2 + 1)
        left = (scr.cols - w) // 2 + 1
        draw_box(scr, top, left, w, h, title=title, right=right)
        return top, left, w, h

    def draw_add(self, scr: Screen) -> None:
        top, left, w, h = self.modal(scr, 96, 12, "Add torrent")
        ix, iw = left + 2, w - 4
        scr.put(top + 2, ix, Line(iw).add("Magnet link  ·  .torrent URL  ·  path to a .torrent file", T.text_dim))
        draw_box(scr, top + 3, ix, iw, 3, title="Link or path", color=T.accent)
        field = Line(iw - 4).add("❯ ", T.accent2, bold=True)
        text = clean(self.input_buf)
        room = iw - 4 - 2 - 1
        shown = text if cw(text) <= room else "…" + clip(text[::-1], room - 1)[::-1]
        field.add(shown, T.text, bold=True)
        blink = (not self.cfg.animations) or int(time.monotonic() * 2) % 2 == 0
        field.add("▏" if blink else " ", T.accent2)
        scr.put(top + 4, ix + 2, field)
        scr.put(top + 7, ix, Line(iw).add("Save to  ", T.text_faint).add(fit(str(self.cfg.download_dir), iw - 10), T.text))
        fp = {"multi": "multi-file torrents ask which files to download",
              "always": "every torrent asks which files to download",
              "never": "everything is downloaded (file picker off)"}[self.cfg.file_picker]
        scr.put(top + 8, ix, Line(iw).add("Files    ", T.text_faint).add(fp, T.text_dim))
        if self.vpn_blocked():
            scr.put(top + 9, ix, Line(iw).add("Kill switch is on and no VPN is detected - adding is blocked.", T.err, bold=True))

    def draw_remove(self, scr: Screen) -> None:
        top, left, w, h = self.modal(scr, 80, 11, "Remove torrent")
        ix, iw = left + 2, w - 4
        info = self.backend.torrents.get(self.remove_tid, {})
        scr.put(top + 2, ix, Line(iw).add(fit(clean(info.get("name") or self.remove_tid), iw), T.text, bold=True))
        scr.put(top + 4, ix, Line(iw).add(" 1 ", T.key_fg, bold=True, bg=T.key_bg).add("  Remove from list", T.text)
                .add("  (keep downloaded files)", T.text_dim))
        scr.put(top + 6, ix, Line(iw).add(" 2 ", T.pill_fg, bold=True, bg=T.err).add("  Remove and delete files", T.err, bold=True)
                .add("  (cannot be undone)", T.text_dim))
        scr.put(top + 8, ix, Line(iw).add("esc to cancel", T.text_faint))

    def draw_fetch(self, scr: Screen) -> None:
        top, left, w, h = self.modal(scr, 74, 9, "Reading magnet link")
        ix, iw = left + 2, w - 4
        f = self.fetch or {}
        el = time.monotonic() - f.get("t0", time.monotonic())
        scr.put(top + 2, ix, Line(iw).add(self.spinner() + "  ", T.accent, bold=True)
                .add("Asking peers for the file list…", T.text, bold=True))
        draw_bar(l := Line(iw), iw - 12, min(1.0, el / max(1, self.cfg.magnet_timeout)), T.accent, T.accent2, "thin")
        l.add(f"  {int(el):>2}s / {self.cfg.magnet_timeout}s", T.text_dim)
        scr.put(top + 4, ix, l)
        scr.put(top + 6, ix, Line(iw).add("Needs DHT or a tracker in the link. If it takes too long press ⏎ to add everything.", T.text_faint))

    def draw_browse(self, scr: Screen) -> None:
        w = min(scr.cols - 2, 100)
        top, left, w, h = self.modal(scr, w, scr.rows - 2, "Browse", right=".torrent files")
        ix, iw = left + 1, w - 2
        scr.put(top + 1, ix, Line(iw).add(" ").add(fit(str(self.browse_path), iw - 2), T.accent, bold=True).pad())
        hsep(scr, top + 2, left, w)
        list_h = h - 5
        if self.browse_sel < self.scroll:
            self.scroll = self.browse_sel
        if self.browse_sel >= self.scroll + list_h:
            self.scroll = self.browse_sel - list_h + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.browse_entries) - list_h)))
        if not self.browse_entries:
            scr.put(top + 3, ix, Line(iw).add("   (no folders or .torrent files here)", T.text_faint))
        for r in range(list_h):
            idx = self.scroll + r
            if idx >= len(self.browse_entries):
                break
            p = self.browse_entries[idx]
            cur = idx == self.browse_sel
            l = Line(iw, T.sel_bg if cur else None).add("▌" if cur else " ", T.accent2).add(" ")
            if p.is_dir():
                l.add("▸ ", T.accent).add(fit(clean(p.name) + "/", iw - 6), T.sel_fg if cur else T.text, bold=True)
            else:
                l.add("◆ ", T.accent2).add(fit(clean(p.name), iw - 6), T.accent2, bold=cur)
            scr.put(top + 3 + r, ix, l.pad())
        scrollbar(scr, left + w - 1, top + 3, list_h, len(self.browse_entries), list_h, self.scroll)

    def draw_log(self, scr: Screen) -> None:
        top, left, w, h = self.modal(scr, scr.cols - 4, scr.rows - 2, "Log", right=str(LOG_FILE).replace(str(HOME), "~"))
        ix, iw = left + 1, w - 2
        view = h - 2
        lines = self.log_lines
        end = len(lines) - self.log_scroll
        for r, ln in enumerate(lines[max(0, end - view):end]):
            low = ln.lower()
            col = T.err if ("err" in low or "traceback" in low or "failed" in low) else T.text_dim
            scr.put(top + 1 + r, ix, Line(iw).add(" ").add(fit(clean(ln), iw - 2), col))

    def draw_help(self, scr: Screen) -> None:
        left_col = [
            ("Navigate", [("↑ ↓  j k", "move"), ("PgUp PgDn", "page"), ("Home End", "first / last")]),
            ("Torrents", [("a", "add magnet / URL / path"), ("t", "browse for a .torrent"), ("space", "pause ⇄ resume"),
                          ("p  r", "pause / resume"), ("P  R", "pause all / resume all"), ("x  Del", "remove"),
                          ("f", "choose files"), ("⏎", "details panel")]),
        ]
        right_col = [
            ("View", [("s  S", "sort field / reverse"), ("i", "re-check IP + VPN"), ("c", "reload config.ini"),
                      ("l", "show log"), ("h  ?", "this help"), ("q", "quit (deluged keeps running)")]),
            ("File picker", [("space", "toggle file or folder"), ("a  n  i", "all · none · invert"),
                             ("← →", "collapse / expand"), ("e  E", "expand / collapse all"),
                             ("⏎  esc", "confirm / cancel")]),
        ]
        two = scr.cols >= 96
        w = 92 if two else 54
        h = 19 if two else 33
        top, left, w, h = self.modal(scr, w, h, f"{APP_NAME} {VERSION} · help")
        colw = (w - 6) // 2 if two else w - 4

        def column(sections: list, x: int, y: int) -> int:
            for title, rows_ in sections:
                scr.put(y, x, Line(colw).add(title, T.accent, bold=True))
                y += 1
                for k, d in rows_:
                    if y >= top + h - 4:
                        return y
                    scr.put(y, x, Line(colw).add(f"{k:<11}", T.accent2, bold=True).add(d, T.text_dim))
                    y += 1
                y += 1
            return y

        if two:
            column(left_col, left + 2, top + 2)
            column(right_col, left + 3 + colw, top + 2)
        else:
            column(left_col + right_col, left + 2, top + 2)
        short = lambda p: str(p).replace(str(HOME), "~")
        y = top + h - 4
        scr.put(y, left + 2, Line(w - 4).add("config ", T.text_faint).add(short(CONFIG_FILE), T.text_dim))
        scr.put(y + 1, left + 2, Line(w - 4).add("log    ", T.text_faint).add(short(LOG_FILE), T.text_dim))
        scr.put(y + 2, left + 2, Line(w - 4).add("saves  ", T.text_faint).add(short(self.cfg.download_dir), T.text_dim))

    # ----- main loop -----
    def run(self) -> None:
        trim_log()
        log("=== start ===")
        try:
            self.cfg.download_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            log(f"download dir: {e}")
        print(f"{APP_NAME}: connecting to deluged…")
        self.backend.connect()
        self.backend.refresh()
        self.backend.update_external_ip()
        if self.cfg.warnings:
            self.set_status("config.ini: " + self.cfg.warnings[0], "warn", 9)

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        sys.stdout.write(ALT_ENTER + HIDE_CUR + PASTE_ON)
        sys.stdout.flush()
        try:
            tty.setcbreak(fd)
            raw = termios.tcgetattr(fd)
            raw[0] &= ~(termios.IXON | termios.ICRNL | termios.INLCR)
            raw[3] &= ~(termios.ISIG | termios.IEXTEN)
            termios.tcsetattr(fd, termios.TCSADRAIN, raw)
            while self.running:
                try:
                    if self.backend.mode == "rpc" and reactor:
                        reactor.iterate(0)  # type: ignore
                    self.drain_events()
                    self.tick()
                    self.draw()
                except Exception:
                    log_exc("loop")
                    self.set_status("Internal error - see log (l)", "err", 6)
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    for k in self.reader.feed(os.read(fd, 4096)):
                        if k:
                            try:
                                self.handle(k)
                            except Exception:
                                log_exc("handle")
                                self.set_status("Internal error - see log (l)", "err", 6)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            if HAS_RPC and deluge_client:
                try:
                    deluge_client.disconnect()
                except Exception:
                    pass
            sys.stdout.write(RESET + PASTE_OFF + SHOW_CUR + ALT_EXIT)
            sys.stdout.flush()
            log("=== exit ===")


def main() -> None:
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__)
        return
    if "--paths" in args:
        print(f"config: {CONFIG_FILE}\nlog:    {LOG_FILE}\nauth:   {AUTH_FILE}")
        return
    if "--reset-config" in args:
        if CONFIG_FILE.exists():
            bak = CONFIG_FILE.with_suffix(".ini.bak")
            shutil.copy2(CONFIG_FILE, bak)
            print(f"old config saved as {bak}")
        CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
        print(f"fresh config written to {CONFIG_FILE}")
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("TDeluge needs an interactive terminal.", file=sys.stderr)
        sys.exit(1)

    def on_term(signum: int, frame: Any) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is read as a key instead
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)
    cfg = Config()
    App(cfg).run()


if __name__ == "__main__":
    main()
