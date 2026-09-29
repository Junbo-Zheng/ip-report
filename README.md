# ip-report

[![PyPI](https://img.shields.io/pypi/v/ip-report.svg)](https://pypi.org/project/ip-report/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Cross-platform CLI that emails you this machine's IP addresses — on boot, on a schedule, or on demand.

Built for headless boxes on DHCP networks: home servers, Raspberry Pis, remote dev machines whose address you can never remember. One small binary, no daemon, no cloud service.

## Features

- **All interfaces, IPv4 + IPv6** — the report lists every NIC's addresses plus the default-route egress IP, not just one
- **Cross-platform** — Linux, macOS, and Windows, with user-level autostart (no root/admin needed)
- **No daemon** — `ip-report run` does its job and exits; scheduling is delegated to the OS (systemd user timer, LaunchAgent, Windows scheduled task)
- **Secrets stay local** — SMTP credentials live in a TOML config under your user config directory, never in the repo
- **Zero-config dev runs** — `./main.py` runs from source with no install

## Install

Requires Python 3.10+.

```bash
pip install ip-report
# or, isolated:
pipx install ip-report
```

## Quick start

1. Create the config (a fully commented template) and fill in your SMTP credentials:

   ```bash
   ip-report init
   ```

   | Platform | Config path |
   |---|---|
   | Linux | `~/.config/ip-report/config.toml` |
   | macOS | `~/Library/Application Support/ip-report/config.toml` |
   | Windows | `%APPDATA%\ip-report\config.toml` |

2. Send a one-shot report:

   ```bash
   ip-report run
   ```

3. Install boot reporting (generates the OS-native autostart, user level):

   ```bash
   ip-report install
   ```

   To also report periodically, pass an interval in minutes:

   ```bash
   ip-report install --interval 60
   ```

   To also report when the network recovers or the IP changes (checked once a
   minute, sends only on change):

   ```bash
   ip-report install --watch
   ```

If the network is not up yet when `run` fires (right after boot), nothing is
sent; the autostart retries every 30s and reports once the network is up
(Linux/macOS). `run` accepts `--wait SECONDS` (default 60) for how long to
wait first, and `--on-change` to report only on recovery/IP change.

> [!NOTE]
> On Windows, scheduled tasks have no user-level retry-on-failure, so the
> boot report is best-effort (sent once at logon); the `--watch` network
> monitor works on all platforms.

Other commands: `ip-report init` (write a commented config template), `ip-report uninstall` (remove the autostart), `ip-report status` (is it installed?), and `ip-report local` (print the same report content to stdout — host, OS, time, uptime, egress and every interface including VPN tunnels — without sending email).

## Development

```bash
git clone https://github.com/Junbo-Zheng/ip-report
cd ip-report
pip install -e ".[dev]"   # dev tools (pytest, ruff)
pytest                    # runs against src/ directly, no install needed
./main.py --help          # run the CLI from source
```

Lint and format:

```bash
ruff check --fix src tests main.py
ruff format src tests main.py
```

## License

This project is licensed under the Apache License, Version 2.0. See
[LICENSE](LICENSE) or <https://www.apache.org/licenses/LICENSE-2.0> for the
full text.
