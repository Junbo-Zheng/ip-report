# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

from pathlib import Path

import pytest

from ip_report import system


@pytest.fixture
def linux(monkeypatch, tmp_path):
    monkeypatch.setattr(system, "_current_platform", lambda: "Linux")
    monkeypatch.setattr(system, "_require_cli", lambda: "/usr/local/bin/ip-report")
    monkeypatch.setattr(system.os.environ, "get", lambda k, d=None: str(tmp_path))
    return tmp_path


def _run_ok(cmd, check=True):
    return True


def test_install_linux_writes_units_and_timer(monkeypatch, linux):
    monkeypatch.setattr(system, "_run", _run_ok)
    result = system.install(30)
    assert "systemd" in result
    unit_dir = linux / "systemd" / "user"
    service = (unit_dir / "ip-report.service").read_text()
    assert "ExecStart=/usr/local/bin/ip-report run" in service
    # No network at fire time -> `run` exits 1 -> systemd retries.
    assert "Restart=on-failure" in service and "RestartSec=30s" in service
    # Periodic mode: timer owns triggering, service stays uninstalled.
    assert "WantedBy" not in service
    timer = (unit_dir / "ip-report.timer").read_text()
    assert "OnUnitActiveSec=30min" in timer


def test_install_linux_interval_zero_no_timer(monkeypatch, linux):
    monkeypatch.setattr(system, "_run", _run_ok)
    system.install(0)
    unit_dir = linux / "systemd" / "user"
    service = (unit_dir / "ip-report.service").read_text()
    # Boot-only mode: the service itself must be enable-able.
    assert "WantedBy=default.target" in service
    assert not (unit_dir / "ip-report.timer").exists()


def test_install_linux_periodic_to_boot_only_removes_timer(monkeypatch, linux):
    monkeypatch.setattr(system, "_run", _run_ok)
    system.install(60)
    assert (linux / "systemd" / "user" / "ip-report.timer").exists()
    system.install(0)
    assert not (linux / "systemd" / "user" / "ip-report.timer").exists()


def test_install_linux_watch_creates_watch_units(monkeypatch, linux):
    monkeypatch.setattr(system, "_run", _run_ok)
    result = system.install(0, watch=True)
    unit_dir = linux / "systemd" / "user"
    watch_service = (unit_dir / "ip-report-watch.service").read_text()
    assert "run --on-change --wait 5" in watch_service
    assert "Restart" not in watch_service  # watcher exits 0, no retry needed
    assert "OnUnitActiveSec=60s" in (unit_dir / "ip-report-watch.timer").read_text()
    assert "watch=on" in result


def test_uninstall_linux_removes_watch_units(monkeypatch, linux):
    monkeypatch.setattr(system, "_run", _run_ok)
    system.install(0, watch=True)
    system.uninstall()
    unit_dir = linux / "systemd" / "user"
    assert not (unit_dir / "ip-report-watch.timer").exists()
    assert not (unit_dir / "ip-report-watch.service").exists()


def test_uptime_linux(monkeypatch):
    monkeypatch.setattr(system.platform, "system", lambda: "Linux")
    import io

    monkeypatch.setattr("builtins.open", lambda p: io.StringIO("12345.67 99999.00\n"))
    assert system.uptime_seconds() == 12345.67


def test_uptime_unsupported_platform(monkeypatch):
    monkeypatch.setattr(system.platform, "system", lambda: "SunOS")
    assert system.uptime_seconds() is None


def test_require_cli_not_on_path(monkeypatch):
    monkeypatch.setattr(system.shutil, "which", lambda name: None)
    with pytest.raises(system.InstallError, match="not on PATH"):
        system._require_cli()


def test_require_cli_found(monkeypatch):
    monkeypatch.setattr(system.shutil, "which", lambda name: "/usr/bin/ip-report")
    assert system._require_cli() == "/usr/bin/ip-report"


def test_uninstall_linux_removes_units(monkeypatch, linux):
    monkeypatch.setattr(system, "_run", _run_ok)
    system.install(60)
    system.uninstall()
    unit_dir = linux / "systemd" / "user"
    assert not (unit_dir / "ip-report.service").exists()
    assert not (unit_dir / "ip-report.timer").exists()


def test_uninstall_linux_when_not_installed(monkeypatch, linux):
    assert system.uninstall() == "autostart: not installed — nothing to remove"


def test_status_linux_not_installed(linux):
    assert "not installed" in system.status()


def test_status_linux_boot_only(monkeypatch, linux):
    monkeypatch.setattr(system, "_run", _run_ok)
    system.install(0)
    s = system.status()
    assert "installed — boot only" in s and "enabled" in s


def test_status_linux_periodic(monkeypatch, linux):
    monkeypatch.setattr(system, "_run", _run_ok)
    system.install(30)
    s = system.status()
    assert "installed — boot + periodic" in s and "active" in s


def test_os_release_linux(monkeypatch):
    monkeypatch.setattr(system.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        system.platform,
        "freedesktop_os_release",
        lambda: {"PRETTY_NAME": "Ubuntu 22.04.5 LTS"},
    )
    assert system.os_release() == "Ubuntu 22.04.5 LTS"


def test_os_release_linux_missing_os_release(monkeypatch):
    # No /etc/os-release (e.g. some containers) -> kernel-string fallback.
    monkeypatch.setattr(system.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        system.platform,
        "freedesktop_os_release",
        lambda: (_ for _ in ()).throw(OSError("missing")),
    )
    assert system.os_release() == system.platform.platform()


def test_os_release_macos(monkeypatch):
    monkeypatch.setattr(system.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system.platform, "mac_ver", lambda: ("27.0.1", "", "arm64"))
    assert system.os_release() == "macOS 27.0.1"


def test_os_release_windows(monkeypatch):
    monkeypatch.setattr(system.platform, "system", lambda: "Windows")
    monkeypatch.setattr(system.platform, "release", lambda: "11")
    monkeypatch.setattr(system.platform, "version", lambda: "10.0.26100")
    assert "Windows 11" in system.os_release()


def test_install_macos_writes_plist(monkeypatch, tmp_path):
    monkeypatch.setattr(system, "_current_platform", lambda: "Darwin")
    monkeypatch.setattr(system, "_require_cli", lambda: "/usr/local/bin/ip-report")
    monkeypatch.setattr(system, "_run", _run_ok)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = system.install(15)
    plist = tmp_path / "Library" / "LaunchAgents" / system._PLIST_NAME
    content = plist.read_text()
    assert "StartInterval" in content and "900" in content
    assert "RunAtLoad" in content
    # Retry until `run` exits 0 (network up, report sent).
    assert "KeepAlive" in content and "SuccessfulExit" in content
    assert "interval=15min" in result


def test_install_macos_boot_only_has_no_interval(monkeypatch, tmp_path):
    monkeypatch.setattr(system, "_current_platform", lambda: "Darwin")
    monkeypatch.setattr(system, "_require_cli", lambda: "/usr/local/bin/ip-report")
    monkeypatch.setattr(system, "_run", _run_ok)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    system.install(0)
    content = (tmp_path / "Library" / "LaunchAgents" / system._PLIST_NAME).read_text()
    assert "StartInterval" not in content


def test_unsupported_platform(monkeypatch):
    monkeypatch.setattr(system.platform, "system", lambda: "SunOS")
    with pytest.raises(system.InstallError, match="unsupported platform"):
        system.install(60)
