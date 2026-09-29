# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""Install/remove the per-platform user-level autostart integration.

No daemon of our own: Linux gets a systemd user unit + timer, macOS a
LaunchAgent plist, Windows a scheduled task. All user-level, no root.
"""

import logging
import os
import platform
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_UNIT_NAME = "ip-report"
_PLIST_NAME = "com.github.junbo-zheng.ip-report.plist"
_PLIST_WATCH_NAME = "com.github.junbo-zheng.ip-report.watch.plist"
_WIN_TASK_BOOT = "ip-report-boot"
_WIN_TASK_PERIODIC = "ip-report-periodic"
_WIN_TASK_WATCH = "ip-report-watch"


def os_release() -> str:
    """Human-friendly OS/distro name, falling back to the kernel string.

    platform.platform() only carries the kernel; users expect the product
    name ("Ubuntu 22.04.5 LTS", "macOS 27.0.1", "Windows 11").
    """
    system = platform.system()
    try:
        if system == "Linux":
            # freedesktop_os_release() reads /etc/os-release (PEP 724).
            rel = platform.freedesktop_os_release()
            return rel.get("PRETTY_NAME") or rel.get("NAME") or platform.platform()
        if system == "Darwin":
            ver, _codename, _arch = platform.mac_ver()
            return f"macOS {ver}" if ver else platform.platform()
        if system == "Windows":
            return f"Windows {platform.release()} (build {platform.version()})"
    except (OSError, AttributeError, ValueError) as e:
        logger.debug("os release lookup failed: %s", e)
    return platform.platform()


def uptime_seconds() -> float | None:
    """Seconds since boot, or None if unavailable on this platform."""
    system = platform.system()
    try:
        if system == "Linux":
            # First field of /proc/uptime is the uptime in seconds.
            with open("/proc/uptime") as f:
                return float(f.read().split()[0])
        if system == "Darwin":
            # kern.boottime prints 'sec = 1234567890, usec = ...' in localtime.
            out = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            boot = float(out.split("sec =")[1].split(",")[0])
            return time.time() - boot
        if system == "Windows":
            import ctypes

            return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        logger.warning("uptime lookup failed: %s", e)
        return None
    logger.warning("uptime not supported on %s", system)
    return None


class InstallError(Exception):
    """Raised when the autostart integration cannot be installed/removed."""


def _current_platform() -> str:
    system = platform.system()
    if system not in ("Linux", "Darwin", "Windows"):
        raise InstallError(f"unsupported platform: {system}")
    return system


def _require_cli() -> str:
    exe = shutil.which("ip-report")
    if exe is None:
        raise InstallError(
            "ip-report is not on PATH — pip install ip-report first (or the autostart "
            "would point at nothing)"
        )
    return exe


def install(interval_minutes: int, watch: bool = False) -> str:
    """Install autostart; returns a short human-readable result line."""
    system = _current_platform()
    exe = _require_cli()
    if system == "Linux":
        return _install_linux(exe, interval_minutes, watch)
    if system == "Darwin":
        return _install_macos(exe, interval_minutes, watch)
    return _install_windows(exe, interval_minutes, watch)


def uninstall() -> str:
    system = _current_platform()
    if system == "Linux":
        return _uninstall_linux()
    if system == "Darwin":
        return _uninstall_macos()
    return _uninstall_windows()


_NOT_INSTALLED = (
    "autostart: not installed — run 'ip-report install' to enable boot reporting"
)


def status() -> str:
    """One human-readable line describing the autostart state."""
    system = _current_platform()
    if system == "Linux":
        unit_dir = Path(
            os.path.join(
                os.environ.get("XDG_CONFIG_HOME", "~/.config"), "systemd", "user"
            )
        ).expanduser()
        service = unit_dir / f"{_UNIT_NAME}.service"
        timer = unit_dir / f"{_UNIT_NAME}.timer"
        if not service.exists():
            return _NOT_INSTALLED
        if timer.exists():
            active = _run(
                ["systemctl", "--user", "is-active", f"{_UNIT_NAME}.timer"],
                check=False,
            )
            return (
                f"autostart: installed — boot + periodic (systemd user timer, "
                f"{'active' if active else 'inactive'}, at {timer})"
            )
        enabled = _run(
            ["systemctl", "--user", "is-enabled", f"{_UNIT_NAME}.service"],
            check=False,
        )
        return (
            f"autostart: installed — boot only (systemd user service, "
            f"{'enabled' if enabled else 'disabled'}, at {service})"
        )
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / _PLIST_NAME
        if not plist.exists():
            return _NOT_INSTALLED
        return f"autostart: installed — boot (LaunchAgent, loaded at login, at {plist})"
    if _run(["schtasks", "/query", "/tn", _WIN_TASK_BOOT], check=False):
        return (
            f"autostart: installed — boot (Windows scheduled task '{_WIN_TASK_BOOT}')"
        )
    return _NOT_INSTALLED


# --- Linux (systemd user unit + timer) ---

_WATCH_UNIT = "ip-report-watch"


def _install_linux(exe: str, interval_minutes: int, watch: bool = False) -> str:
    unit_dir = Path(
        os.path.join(os.environ.get("XDG_CONFIG_HOME", "~/.config"), "systemd", "user")
    ).expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)

    # Boot-only mode enables the service itself, so it needs an [Install]
    # section (without one systemctl leaves it "static" and it never starts).
    # Periodic mode keeps the service uninstalled and lets the timer own it —
    # a WantedBy here would make default.target AND the timer both fire it.
    install_sec = (
        "\n[Install]\nWantedBy=default.target\n" if interval_minutes == 0 else ""
    )
    # `run` exits 1 when the network is not up yet; on-failure restart makes
    # systemd retry every 30s until the report goes out once, then stop.
    # StartLimitIntervalSec=0 disables the default burst limit so a slow DHCP
    # (minutes) can never exhaust the retries.
    service = textwrap.dedent(f"""\
        [Unit]
        Description=ip-report: email this machine's IP addresses

        [Service]
        Type=oneshot
        ExecStart={exe} run
        Restart=on-failure
        RestartSec=30s
        StartLimitIntervalSec=0{install_sec}""")
    (unit_dir / f"{_UNIT_NAME}.service").write_text(service)

    # Switching from periodic to boot-only must tear the old timer down,
    # otherwise it stays enabled and keeps reporting on the old schedule.
    timer_path = unit_dir / f"{_UNIT_NAME}.timer"
    if interval_minutes == 0 and timer_path.exists():
        _run(
            ["systemctl", "--user", "disable", "--now", f"{_UNIT_NAME}.timer"],
            check=False,
        )
        timer_path.unlink()

    targets = [f"{_UNIT_NAME}.service"]
    if interval_minutes > 0:
        timer = textwrap.dedent(f"""\
            [Unit]
            Description=ip-report: periodic IP report timer

            [Timer]
            OnBootSec=2min
            OnUnitActiveSec={interval_minutes}min

            [Install]
            WantedBy=timers.target
        """)
        (unit_dir / f"{_UNIT_NAME}.timer").write_text(timer)
        targets = [f"{_UNIT_NAME}.timer"]

    if watch:
        # The watcher exits 0 whether or not it reported, so no restart is
        # needed — the timer just re-fires it every minute.
        (unit_dir / f"{_WATCH_UNIT}.service").write_text(
            textwrap.dedent(f"""\
            [Unit]
            Description=ip-report: report on network recovery / IP change

            [Service]
            Type=oneshot
            ExecStart={exe} run --on-change --wait 5
        """)
        )
        (unit_dir / f"{_WATCH_UNIT}.timer").write_text(
            textwrap.dedent("""\
            [Unit]
            Description=ip-report: network watch timer

            [Timer]
            OnBootSec=30s
            OnUnitActiveSec=60s

            [Install]
            WantedBy=timers.target
        """)
        )
        targets.append(f"{_WATCH_UNIT}.timer")

    _run(["systemctl", "--user", "daemon-reload"])
    for target in targets:
        _run(["systemctl", "--user", "enable", "--now", target])
    result = (
        f"systemd user units installed in {unit_dir} (interval={interval_minutes}min"
    )
    return result + ", watch=on)" if watch else result + ")"


def _uninstall_linux() -> str:
    unit_dir = Path(
        os.path.join(os.environ.get("XDG_CONFIG_HOME", "~/.config"), "systemd", "user")
    ).expanduser()
    units = [
        unit_dir / f"{_UNIT_NAME}.timer",
        unit_dir / f"{_UNIT_NAME}.service",
        unit_dir / f"{_WATCH_UNIT}.timer",
        unit_dir / f"{_WATCH_UNIT}.service",
    ]
    if not any(u.exists() for u in units):
        return "autostart: not installed — nothing to remove"
    for unit_path in units:
        _run(["systemctl", "--user", "disable", "--now", unit_path.name], check=False)
        unit_path.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    return "systemd user units removed"


# --- macOS (LaunchAgent) ---


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / _PLIST_NAME


def _watch_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / _PLIST_WATCH_NAME


def _install_macos(exe: str, interval_minutes: int, watch: bool = False) -> str:
    plist = _plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    interval_xml = (
        f"    <key>StartInterval</key>\n"
        f"    <integer>{interval_minutes * 60}</integer>\n"
        if interval_minutes > 0
        else ""
    )
    plist.write_text(
        textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>com.github.junbo-zheng.ip-report</string>
            <key>ProgramArguments</key>
            <array>
                <string>{exe}</string>
                <string>run</string>
            </array>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <dict>
                <key>SuccessfulExit</key>
                <false/>
            </dict>
        {interval_xml}</dict>
        </plist>
    """)
    )
    _run(["launchctl", "unload", str(plist)], check=False)
    _run(["launchctl", "load", str(plist)])
    watch_plist = _watch_plist_path()
    if watch:
        # Watcher exits 0 always, so no KeepAlive — StartInterval just re-fires.
        watch_plist.write_text(
            textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
              "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
            <dict>
                <key>Label</key>
                <string>com.github.junbo-zheng.ip-report.watch</string>
                <key>ProgramArguments</key>
                <array>
                    <string>{exe}</string>
                    <string>run</string>
                    <string>--on-change</string>
                    <string>--wait</string>
                    <string>5</string>
                </array>
                <key>RunAtLoad</key>
                <true/>
                <key>StartInterval</key>
                <integer>60</integer>
            </dict>
            </plist>
        """)
        )
        _run(["launchctl", "unload", str(watch_plist)], check=False)
        _run(["launchctl", "load", str(watch_plist)])
    elif watch_plist.exists():
        _run(["launchctl", "unload", str(watch_plist)], check=False)
        watch_plist.unlink()
    result = f"LaunchAgent installed at {plist} (interval={interval_minutes}min"
    return result + ", watch=on)" if watch else result + ")"


def _uninstall_macos() -> str:
    plist = _plist_path()
    watch_plist = _watch_plist_path()
    if not plist.exists() and not watch_plist.exists():
        return "autostart: not installed — nothing to remove"
    for p in (plist, watch_plist):
        if p.exists():
            _run(["launchctl", "unload", str(p)], check=False)
            p.unlink()
    return "LaunchAgent removed"


# --- Windows (scheduled tasks) ---


def _install_windows(exe: str, interval_minutes: int, watch: bool = False) -> str:
    # /sc onlogon keeps this user-level (onstart would require elevation).
    _run(
        [
            "schtasks",
            "/create",
            "/f",
            "/tn",
            _WIN_TASK_BOOT,
            "/sc",
            "onlogon",
            "/tr",
            f'"{exe}" run',
        ],
    )
    if interval_minutes > 0:
        _run(
            [
                "schtasks",
                "/create",
                "/f",
                "/tn",
                _WIN_TASK_PERIODIC,
                "/sc",
                "minute",
                "/mo",
                str(interval_minutes),
                "/tr",
                f'"{exe}" run',
            ],
        )
    else:
        _run(["schtasks", "/delete", "/f", "/tn", _WIN_TASK_PERIODIC], check=False)
    if watch:
        _run(
            [
                "schtasks",
                "/create",
                "/f",
                "/tn",
                _WIN_TASK_WATCH,
                "/sc",
                "minute",
                "/mo",
                "1",
                "/tr",
                f'"{exe}" run --on-change --wait 5',
            ],
        )
    else:
        _run(["schtasks", "/delete", "/f", "/tn", _WIN_TASK_WATCH], check=False)
    return (
        f"scheduled tasks installed (interval={interval_minutes}min, "
        f"watch={'on' if watch else 'off'})"
    )


def _uninstall_windows() -> str:
    if not _run(["schtasks", "/query", "/tn", _WIN_TASK_BOOT], check=False):
        return "autostart: not installed — nothing to remove"
    for task in (_WIN_TASK_BOOT, _WIN_TASK_PERIODIC, _WIN_TASK_WATCH):
        _run(["schtasks", "/delete", "/f", "/tn", task], check=False)
    return "scheduled tasks removed"


def _run(cmd: list[str], check: bool = True) -> bool:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise InstallError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}"
        )
    return result.returncode == 0
