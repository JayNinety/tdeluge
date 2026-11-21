# TDeluge

A modern terminal client for the [Deluge](https://deluge-torrent.org) BitTorrent daemon. One Python file, raw ANSI, transparent background, truecolor gradients.

## Features

- **File picker** like deluge-gtk: a folder tree with tri-state checkboxes, select all / none / invert, per-file sizes, and a live "selected X of Y" gauge. Shown when you add a multi-file `.torrent` or magnet link, and available any time with `f` on an existing torrent.
- **Real IP in the header**: your public IP (with city and ISP), whether a **VPN interface** is up, what **Deluge itself** reports as its external IP (with a match/mismatch marker), and the interface it is bound to.
- **Optional kill switch**: pauses everything and blocks adding torrents when no VPN interface is detected.
- **Torrent cards** with gradient progress bars, state icons, speeds, ETA, peers, ratio, plus speed sparklines and a details panel. A one-line compact layout is available.
- **`config.ini`** for Deluge options (sequential download, stop at ratio, speed limits, move on completion...), daemon settings (bind to a VPN interface, DHT, encryption...), sort order, and every UI color. Seven built-in themes.
- Paste support, `.torrent` browser, torrent URLs, auto-start of `deluged`, auto-reconnect, and an error log.

## Requirements

| Needed | Why |
|---|---|
| Python 3.9+ | runs the client |
| Deluge 2.x (`deluged` + its Python modules) | the daemon and the RPC client library (`deluge.ui.client`); pulls in Twisted and libtorrent |
| A truecolor terminal and a font with box-drawing and block characters | the UI (kitty, alacritty, wezterm, foot, gnome-terminal, konsole, iTerm2...) |
| `curl` (optional) | fallback for the public-IP lookup |

The Python modules must be importable by the Python that runs the script. If you use a virtualenv, create it with `--system-site-packages`. Without the modules TDeluge falls back to a limited `deluge-console` mode, with no file picker.

### Install

**Debian / Ubuntu**
```bash
sudo apt install python3 deluged deluge-console curl
```

**Arch Linux**
```bash
sudo pacman -S python deluge curl
```

**Fedora**
```bash
sudo dnf install python3 deluge-daemon deluge-console curl
```

## Usage

```bash
chmod +x tdeluge.py
./tdeluge.py            # or: python3 tdeluge.py
```

It starts `deluged` if it is not running, logs in with the `localclient` entry from `~/.config/deluge/auth`, and opens the UI. Quitting leaves the daemon running, so downloads continue.

Press `a`, paste a magnet link, a `.torrent` URL or a path, and press Enter. If the torrent has several files, the file picker opens first.

| Key | Action |
|---|---|
| `↑ ↓` `j k` | move |
| `a` | add magnet / URL / path |
| `t` | browse for a `.torrent` file |
| `space` | pause / resume selected (`p` / `r` do one or the other) |
| `P` / `R` | pause all / resume all |
| `f` | choose files of the selected torrent |
| `Enter` | toggle details panel |
| `x` / `Del` | remove (keep or delete data) |
| `s` / `S` | cycle sort field / reverse order |
| `i` | re-check IP and VPN |
| `c` | reload `config.ini` live |
| `l` | show the log |
| `h` | help |
| `q` | quit |

In the file picker: `space` toggles a file or folder, `a` / `n` / `i` select all / none / invert, `← →` collapse and expand folders, `Enter` confirms, `Esc` cancels.

Other commands: `tdeluge.py --paths` prints the file locations, and `tdeluge.py --reset-config` backs up and regenerates `config.ini`.

## Configuration

On first run TDeluge creates `~/.config/tdeluge/` containing:

- `config.ini`: every option is documented inline. Edit it, then press `c` in the app.
- `tdeluge.log`: errors and events (trimmed automatically).

A few common tweaks:

```ini
[session]
# bind Deluge to your VPN so nothing leaks if it drops
outgoing_interface = tun0

[vpn]
kill_switch = true

[deluge]
sequential_download = true
stop_at_ratio = true
stop_ratio = 2.0

[ui]
theme = nord        # amethyst | ocean | ember | forest | rose | nord | mono
layout = compact

[colors]
accent = #ff79c6
```

Settings in `[session]` are pushed to the daemon only when filled in; empty means Deluge keeps its own value.

## How it works

TDeluge is a thin client. `deluged` does all the torrenting, and TDeluge talks to it over Deluge's RPC (port 58846) using Deluge's own Python client, polling the torrent list about once a second.

- **File picker:** for a `.torrent` it reads the file list from the file itself. For a magnet link it asks the daemon to fetch the metadata from peers (`prefetch_magnet_metadata`), shows the picker, then adds the torrent with your selection. For magnets the selection is applied the moment the metadata arrives, so a few pieces of unselected files may download briefly before it takes effect.
- **IP / VPN:** the public IP comes from a lookup on a background thread, so the UI never blocks. The VPN indicator checks for a network interface whose name starts with a configured prefix (`tun`, `wg`, `nordlynx`, ...). The "Deluge" IP is what libtorrent reports, which is the address peers actually see, and it only appears once Deluge has connected to a tracker or peer.

## Troubleshooting

- **"Not connected to deluged":** run `deluged` manually and check `~/.config/deluge/auth` has a `localclient` line.
- **IP shows "lookup failed":** press `l` for the reason, or check your firewall or VPN DNS.
- **No VPN detected though one is active:** add your interface name prefix to `[vpn] interfaces` (see `ip link`).
- **Garbled symbols:** use a font with box-drawing and block characters (any Nerd Font or DejaVu Sans Mono works).

## License

GPL-3.0-or-later. See `LICENSE`.
