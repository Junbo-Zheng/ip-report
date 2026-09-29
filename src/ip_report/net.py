# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""Collect this machine's IP addresses without third-party dependencies."""

import logging
import shutil
import socket
import subprocess
import time
import urllib.request

logger = logging.getLogger(__name__)

_EGRESS_TARGETS = {
    socket.AF_INET: ("8.8.8.8", 80),
    socket.AF_INET6: ("2001:4860:4860::8888", 80),
}


def egress_ip(family: int = socket.AF_INET) -> str | None:
    """Default-route egress address via a UDP connect (no packet is sent)."""
    target = _EGRESS_TARGETS.get(family)
    if target is None:
        raise ValueError(f"unsupported address family {family}")
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.connect(target)
            return s.getsockname()[0]
    except OSError as e:
        logger.debug("egress lookup failed for family %s: %s", family, e)
        return None


def public_ip(timeout: float = 5.0) -> str | None:
    """The public (NAT-egress) IPv4 as seen from the internet, or None.

    LAN addresses are useless for locating a box behind NAT; this one GET
    answers "which public IP did my traffic come from".
    """
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=timeout) as r:
            addr = r.read().decode().strip()
            return addr or None
    except (OSError, ValueError) as e:
        logger.debug("public ip lookup failed: %s", e)
        return None


def wait_for_network(timeout: float = 60.0, poll: float = 5.0) -> str | None:
    """Block until the IPv4 egress route is up, or timeout elapses."""
    deadline = time.monotonic() + timeout
    while True:
        ip = egress_ip(socket.AF_INET)
        if ip is not None:
            return ip
        if time.monotonic() >= deadline:
            logger.warning("network not ready after %.0fs, reporting anyway", timeout)
            return None
        logger.debug("network not ready, retrying in %.0fs", poll)
        time.sleep(poll)


def local_addresses(include_ipv6: bool = True) -> list[tuple[str, int, str]]:
    """All addresses the host's name resolves to, as (name, family, addr).

    The hostname lookup is the portable way to enumerate NIC addresses without
    psutil or per-OS ioctls; link-local and loopback entries are skipped.
    """
    families = [socket.AF_INET] + ([socket.AF_INET6] if include_ipv6 else [])
    results: list[tuple[str, int, str]] = []
    try:
        infos = socket.getaddrinfo(
            socket.gethostname(), None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        logger.warning("hostname address lookup failed: %s", e)
        return results
    seen: set[tuple[int, str]] = set()
    for family, _type, _proto, _canon, sockaddr in infos:
        if family not in families:
            continue
        addr = sockaddr[0]
        if addr.startswith("fe80:") or addr == "127.0.0.1" or addr == "::1":
            continue
        if (family, addr) in seen:
            continue
        seen.add((family, addr))
        results.append((socket.gethostname(), family, addr))
    return results


def interface_addresses(include_ipv6: bool = True) -> list[tuple[str, int, str]]:
    """All interface addresses as (iface, family, addr), including VPN tunnels.

    Enumerates per-interface addresses so VPN tunnels (tun/wg on Linux,
    utun on macOS) are visible, unlike the hostname lookup in
    :func:`local_addresses` which only sees addresses the hostname resolves
    to. Uses ``ip -brief address`` on Linux, ``ifconfig`` on macOS, and falls
    back to the hostname lookup when neither tool exists. Loopback and
    link-local entries are skipped.
    """
    if shutil.which("ip") is not None:
        return _addresses_from_ip(include_ipv6)
    if shutil.which("ifconfig") is not None:
        return _addresses_from_ifconfig(include_ipv6)
    return local_addresses(include_ipv6)


def _wanted_families(include_ipv6: bool) -> list[int]:
    return [socket.AF_INET] + ([socket.AF_INET6] if include_ipv6 else [])


def _skip(addr: str, include_ipv6: bool) -> bool:
    return (
        addr.startswith("fe80:")
        or addr == "127.0.0.1"
        or addr == "::1"
        or (socket.AF_INET6 if ":" in addr else socket.AF_INET)
        not in _wanted_families(include_ipv6)
    )


def _addresses_from_ip(include_ipv6: bool) -> list[tuple[str, int, str]]:
    try:
        out = subprocess.run(
            ["ip", "-brief", "address"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("ip -brief address failed, falling back to hostname: %s", e)
        return local_addresses(include_ipv6)

    results: list[tuple[str, int, str]] = []
    for line in out.splitlines():
        # Columns: <iface> <UP|DOWN> [addresses...] — VPN addresses appear
        # like 10.x.x.x or fd..:.., with scope/peer suffixes to strip.
        parts = line.split()
        if len(parts) < 3:
            continue
        iface = parts[0]
        for token in parts[2:]:
            addr = token.split("/")[0]
            if _skip(addr, include_ipv6):
                continue
            family = socket.AF_INET6 if ":" in addr else socket.AF_INET
            results.append((iface, family, addr))
    return results


def _addresses_from_ifconfig(include_ipv6: bool) -> list[tuple[str, int, str]]:
    # macOS: no iproute2 `ip`, but /sbin/ifconfig is always present. Output is
    # blocks per interface — "utun0: flags=..." starts a block, then
    # tab-indented "inet 10.x.x.x ..." / "inet6 fe80::1%utun0 ..." lines.
    try:
        out = subprocess.run(
            ["ifconfig"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("ifconfig failed, falling back to hostname: %s", e)
        return local_addresses(include_ipv6)

    results: list[tuple[str, int, str]] = []
    iface = ""
    for line in out.splitlines():
        if line and not line[0].isspace():
            iface = line.split(":", 1)[0]
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("inet", "inet6"):
            # Strip the %zone suffix macOS appends to link-local IPv6.
            addr = parts[1].split("%", 1)[0]
            if _skip(addr, include_ipv6):
                continue
            family = socket.AF_INET6 if parts[0] == "inet6" else socket.AF_INET
            results.append((iface, family, addr))
    return results
