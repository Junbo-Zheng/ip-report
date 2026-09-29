# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

import re

import pytest

from ip_report import __version__
from ip_report.cli import main


def test_install_interval_defaults_to_boot_only(monkeypatch, capsys):
    calls = {}
    monkeypatch.setattr("ip_report.cli.load_config", lambda: None)
    monkeypatch.setattr(
        "ip_report.cli.system.install",
        lambda interval, watch=False: calls.setdefault("i", interval) or "installed",
    )
    assert main(["install"]) == 0
    assert calls["i"] == 0
    assert "installed" in capsys.readouterr().out


def test_install_interval_flag(monkeypatch, capsys):
    calls = {}
    monkeypatch.setattr("ip_report.cli.load_config", lambda: None)
    monkeypatch.setattr(
        "ip_report.cli.system.install",
        lambda interval, watch=False: calls.setdefault("i", interval) or "installed",
    )
    assert main(["install", "--interval", "30"]) == 0
    assert calls["i"] == 30


def test_install_watch_flag(monkeypatch, capsys):
    calls = {}
    monkeypatch.setattr("ip_report.cli.load_config", lambda: None)
    monkeypatch.setattr(
        "ip_report.cli.system.install",
        lambda interval, watch=False: calls.update(i=interval, w=watch) or "installed",
    )
    assert main(["install", "--watch"]) == 0
    assert calls == {"i": 0, "w": True}


def test_install_rejects_negative_interval():
    assert main(["install", "--interval", "-5"]) == 2


def test_version():
    # Shape check only — pinning the exact number here breaks every bump.
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_and_exits(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help():
    assert main([]) == 2


def test_run_no_network_exits_1_without_email(monkeypatch):
    # Boot-time case: network never came up during the wait. No email must
    # go out (exit 1 drives the systemd/LaunchAgent retry instead).
    monkeypatch.setattr("ip_report.cli.load_config", lambda: None)
    monkeypatch.setattr("ip_report.cli.net.wait_for_network", lambda timeout: None)
    sent = []
    monkeypatch.setattr("ip_report.cli.mail.send_report", lambda *a: sent.append(a))
    assert main(["run", "--wait", "0"]) == 1
    assert sent == []


class _FakeState:
    def __init__(self):
        self.d = {}

    def load(self):
        return self.d

    def save(self, online, last_ip):
        self.d = {"online": online, "last_ip": last_ip}


def _run_on_change(monkeypatch, egress, st):
    from ip_report.config import Config, ReportConfig, SmtpConfig

    cfg = Config(
        smtp=SmtpConfig(
            server="s",
            port=465,
            username="u",
            password="p",
            from_addr="u@e.com",
            to=["r@e.com"],
        ),
        report=ReportConfig(include_ipv6=False),
    )
    monkeypatch.setattr("ip_report.cli.load_config", lambda: cfg)
    monkeypatch.setattr("ip_report.cli.net.wait_for_network", lambda timeout: egress)
    monkeypatch.setattr("ip_report.cli.state.load_state", st.load)
    monkeypatch.setattr("ip_report.cli.state.save_state", st.save)
    sent = []
    monkeypatch.setattr("ip_report.cli.mail.send_report", lambda *a: sent.append(a))
    monkeypatch.setattr("ip_report.cli.net.public_ip", lambda: None)
    code = main(["run", "--on-change", "--wait", "0"])
    return code, sent


def test_run_on_change_first_run_reports(monkeypatch):
    st = _FakeState()  # empty state = first run
    code, sent = _run_on_change(monkeypatch, "10.0.0.1", st)
    assert code == 0 and len(sent) == 1
    assert st.d == {"online": True, "last_ip": "10.0.0.1"}


def test_run_on_change_unchanged_silent(monkeypatch):
    st = _FakeState()
    st.d = {"online": True, "last_ip": "10.0.0.1"}
    code, sent = _run_on_change(monkeypatch, "10.0.0.1", st)
    assert code == 0 and sent == []


def test_run_on_change_ip_change_reports(monkeypatch):
    st = _FakeState()
    st.d = {"online": True, "last_ip": "10.0.0.1"}
    code, sent = _run_on_change(monkeypatch, "10.0.0.2", st)
    assert code == 0 and len(sent) == 1
    assert st.d["last_ip"] == "10.0.0.2"


def test_run_on_change_recovery_reports(monkeypatch):
    # Was marked offline by a previous watch tick, network is back.
    st = _FakeState()
    st.d = {"online": False, "last_ip": "10.0.0.1"}
    code, sent = _run_on_change(monkeypatch, "10.0.0.1", st)
    assert code == 0 and len(sent) == 1


def test_run_on_change_offline_records_and_exits_0(monkeypatch):
    monkeypatch.setattr("ip_report.cli.load_config", lambda: None)
    monkeypatch.setattr("ip_report.cli.net.wait_for_network", lambda timeout: None)
    st = _FakeState()
    st.d = {"online": True, "last_ip": "10.0.0.1"}
    monkeypatch.setattr("ip_report.cli.state.load_state", st.load)
    monkeypatch.setattr("ip_report.cli.state.save_state", st.save)
    sent = []
    monkeypatch.setattr("ip_report.cli.mail.send_report", lambda *a: sent.append(a))
    assert main(["run", "--on-change", "--wait", "0"]) == 0
    assert sent == []
    assert st.d == {"online": False, "last_ip": "10.0.0.1"}


def test_local_prints_system_header(capsys, monkeypatch):
    # No real HTTP in tests — offline runs and CI must not flake on it.
    monkeypatch.setattr("ip_report.cli.net.public_ip", lambda: "203.0.113.7")
    assert main(["local"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    for key in (
        "host",
        "os",
        "time",
        "uptime",
        "public ip",
        "version",
        "egress ipv4",
        "egress ipv6",
        "interfaces",
    ):
        # Keys are column-aligned, so match "key : value", not "key: value".
        assert any(re.match(rf"^{re.escape(key)}\s*:", ln) for ln in lines), key
