# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

import ipaddress
import socket

import pytest

from ip_report import net


def test_egress_ip_returns_address_or_none():
    # On CI/dev machines the route to 8.8.8.8 exists; if it genuinely does
    # not, egress_ip returns None — both outcomes are valid, never an raise.
    ip = net.egress_ip(socket.AF_INET)
    if ip is not None:
        assert isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address)


def test_egress_ip_bad_family():
    with pytest.raises(ValueError):
        net.egress_ip(socket.AF_UNIX)


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_public_ip(monkeypatch):
    monkeypatch.setattr(
        net.urllib.request, "urlopen", lambda url, timeout: _FakeResp(b"203.0.113.7\n")
    )
    assert net.public_ip() == "203.0.113.7"


def test_public_ip_network_error(monkeypatch):
    def boom(url, timeout):
        raise OSError("no route")

    monkeypatch.setattr(net.urllib.request, "urlopen", boom)
    assert net.public_ip() is None


def test_wait_for_network_immediate(monkeypatch):
    monkeypatch.setattr(net, "egress_ip", lambda family: "192.0.2.10")
    assert net.wait_for_network(timeout=0) == "192.0.2.10"


def test_wait_for_network_timeout(monkeypatch):
    monkeypatch.setattr(net, "egress_ip", lambda family: None)
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    assert net.wait_for_network(timeout=0) is None


def test_local_addresses_filters_loopback_and_linklocal():
    addrs = net.local_addresses(include_ipv6=True)
    for _name, _family, addr in addrs:
        assert addr not in ("127.0.0.1", "::1")
        assert not addr.startswith("fe80:")


# Fake `ip -brief address` output covering the cases interface_addresses()
# must handle: physical NIC, VPN tunnel, docker bridge, loopback (skipped),
# link-local IPv6 (skipped), CIDR-suffix stripping.
_FAKE_IP_BRIEF = """\
lo               UNKNOWN        127.0.0.1/8 ::1/128
eno1             UP             10.0.0.5/24 2408:1::1/64 fe80::1/64
tun0             UNKNOWN        10.8.0.2/24
docker0          DOWN           172.17.0.1/16
wg0              DOWN
"""


class _FakeCompleted:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_interface_addresses_parses_ip_brief(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda name: "/usr/sbin/ip")
    monkeypatch.setattr(
        net.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(_FAKE_IP_BRIEF),
    )
    addrs = net.interface_addresses(include_ipv6=True)
    assert addrs == [
        ("eno1", socket.AF_INET, "10.0.0.5"),
        ("eno1", socket.AF_INET6, "2408:1::1"),
        ("tun0", socket.AF_INET, "10.8.0.2"),
        ("docker0", socket.AF_INET, "172.17.0.1"),
    ]


def test_interface_addresses_ipv4_only(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda name: "/usr/sbin/ip")
    monkeypatch.setattr(
        net.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(_FAKE_IP_BRIEF),
    )
    addrs = net.interface_addresses(include_ipv6=False)
    assert addrs == [
        ("eno1", socket.AF_INET, "10.0.0.5"),
        ("tun0", socket.AF_INET, "10.8.0.2"),
        ("docker0", socket.AF_INET, "172.17.0.1"),
    ]


def test_interface_addresses_falls_back_without_ip(monkeypatch):
    monkeypatch.setattr(net.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        net,
        "local_addresses",
        lambda include_ipv6: [("host", socket.AF_INET, "10.0.0.5")],
    )
    assert net.interface_addresses(include_ipv6=True) == [
        ("host", socket.AF_INET, "10.0.0.5")
    ]


# Fake `ifconfig` output (macOS): block per interface, tab-indented address
# lines. Covers en0, a VPN utun, loopback (skipped), link-local with %zone
# suffix (skipped), and a global IPv6 with the zone suffix to strip.
_FAKE_IFCONFIG = """\
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
\tinet6 ::1 prefixlen 128
\tinet6 fe80::1%lo0 prefixlen 64 scopeid 0x1
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 10.225.225.153 netmask 0xffffff00 broadcast 10.225.225.255
\tinet6 2408:1::2%en0 prefixlen 64 autoconf secured
\tinet6 fe80::abcd%en0 prefixlen 64 scopeid 0x9
utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
\tinet 10.8.0.2 --> 10.8.0.1 netmask 0xffffff00
"""


def test_interface_addresses_parses_ifconfig(monkeypatch):
    monkeypatch.setattr(
        net.shutil, "which", lambda name: None if name == "ip" else "/sbin/ifconfig"
    )
    monkeypatch.setattr(
        net.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(_FAKE_IFCONFIG),
    )
    addrs = net.interface_addresses(include_ipv6=True)
    assert addrs == [
        ("en0", socket.AF_INET, "10.225.225.153"),
        ("en0", socket.AF_INET6, "2408:1::2"),
        ("utun3", socket.AF_INET, "10.8.0.2"),
    ]


def test_local_addresses_no_duplicates():
    addrs = net.local_addresses(include_ipv6=True)
    seen = [(f, a) for _n, f, a in addrs]
    assert len(seen) == len(set(seen))
