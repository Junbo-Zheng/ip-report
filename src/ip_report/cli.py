# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

import argparse
import logging
import os
import platform
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from importlib.metadata import PackageNotFoundError, version

from ip_report import config, mail, net, state, system
from ip_report.config import ConfigError, load_config

logger = logging.getLogger(__name__)


def _version() -> str:
    # Installed metadata is the source of truth; fall back to the in-tree
    # __init__ string for zero-install runs (main.py shim, editable checkouts).
    try:
        return version("ip-report")
    except PackageNotFoundError:
        from . import __version__

        return __version__


def main(argv: list[str] | None = None) -> int:
    # Entry point owns logging config — see new-project §Logging. Called once
    # here, never in library modules. Default INFO keeps query commands quiet;
    # set IP_REPORT_LOGLEVEL=DEBUG (or any level) to see the detail logs.
    logging.basicConfig(
        level=os.environ.get("IP_REPORT_LOGLEVEL", "INFO").upper(),
        datefmt="%Y-%m-%d %H:%M:%S",
        # The format string is canonical and kept verbatim (too long for 88).
        format="%(asctime)-19s.%(msecs)03d %(levelname)-8s %(filename)s %(lineno)-3d %(process)d %(message)s",  # noqa: E501
    )
    p = argparse.ArgumentParser(
        prog="ip-report",
        description=f"ip-report {_version()} — email this machine's IP addresses.",
    )
    p.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"ip-report {_version()}",
        help="Show the version and exit",
    )
    sub = p.add_subparsers(dest="command")
    run_p = sub.add_parser(
        "run", help="Collect IPs and send one report email, then exit"
    )
    run_p.add_argument(
        "--wait",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="How long to wait for the network before reporting anyway (default: 60)",
    )
    run_p.add_argument(
        "--on-change",
        action="store_true",
        help="Report only if the network recovered or the egress IP changed since "
        "the last report (state file in the config dir); otherwise exit silently",
    )
    install_p = sub.add_parser(
        "install", help="Install boot autostart integration for this platform"
    )
    install_p.add_argument(
        "--interval",
        type=int,
        default=0,
        metavar="MINUTES",
        help="Also report every MINUTES minutes (default: report at boot only)",
    )
    install_p.add_argument(
        "--watch",
        action="store_true",
        help="Also watch for network recovery / IP change (checks every minute, "
        "reports only when something changed)",
    )
    sub.add_parser("uninstall", help="Remove the autostart integration")
    sub.add_parser("status", help="Show whether the autostart integration is installed")
    sub.add_parser(
        "init", help="Write a commented config template to the user config path"
    )
    sub.add_parser(
        "local",
        help="Print this machine's IP addresses (egress + all interfaces incl. VPN) "
        "to stdout, no email",
    )

    args = p.parse_args(argv)
    if args.command is None:
        p.print_help()
        return 2
    try:
        if args.command == "run":
            return _cmd_run(args.wait, args.on_change)
        if args.command == "install":
            if args.interval < 0:
                logger.error("--interval must be >= 0 (got %d)", args.interval)
                return 2
            # Load config purely to surface a missing/broken one now rather
            # than silently at boot; the interval comes from --interval.
            load_config()
            print(system.install(args.interval, args.watch))
            return 0
        if args.command == "uninstall":
            print(system.uninstall())
            return 0
        if args.command == "init":
            path = config.init_config()
            print(f"template written to {path} — edit it, then run 'ip-report run'")
            return 0
        if args.command == "local":
            return _cmd_local()
        print(system.status())
        return 0
    except (ConfigError, system.InstallError) as e:
        logger.error("%s", e)
        return 1
    except OSError as e:
        logger.error("send failed: %s", e)
        return 1


def _egress_note(addr: str | None, ifaces) -> str:
    """Egress annotation: the interface holding the address, if any."""
    if addr is None:
        return ""
    names = [name for name, _f, a in ifaces if a == addr]
    return f" (via {names[0]})" if names else ""


def _report_body(include_ipv6: bool, egress4: str | None) -> list[str]:
    """The full report lines — the single source shared by run (email) and local.

    Network lookups that can be slow (public-IP HTTP call, IPv6 egress probe)
    run concurrently; keys are column-aligned within each block.
    """
    ifaces = net.interface_addresses(include_ipv6=include_ipv6)
    with ThreadPoolExecutor(max_workers=2) as ex:
        pub_f = ex.submit(net.public_ip)
        eg6_f = ex.submit(net.egress_ip, socket.AF_INET6) if include_ipv6 else None
        uptime = system.uptime_seconds()
        public = pub_f.result()
        egress6 = eg6_f.result() if eg6_f else None

    rows: list[tuple[str, str]] = [
        ("host", platform.node()),
        ("os", f"{platform.system()} ({system.os_release()})"),
        ("time", datetime.now().astimezone().isoformat(timespec="seconds")),
        (
            "uptime",
            str(timedelta(seconds=int(uptime)))
            if uptime is not None
            else "unavailable",
        ),
        ("public ip", public or "unavailable"),
        ("version", f"ip-report {_version()} (Python {platform.python_version()})"),
        ("egress ipv4", (egress4 or "unavailable") + _egress_note(egress4, ifaces)),
    ]
    if include_ipv6:
        rows.append(
            ("egress ipv6", (egress6 or "unavailable") + _egress_note(egress6, ifaces))
        )

    width = max(len(k) for k, _v in rows)
    lines = [f"{k:<{width}}: {v}" for k, v in rows]
    lines.append("interfaces:")
    iface_rows = [
        (name, "ipv6" if family == socket.AF_INET6 else "ipv4", addr)
        for name, family, addr in ifaces
    ]
    if iface_rows:
        name_w = max(len(n) for n, _k, _a in iface_rows)
        lines.extend(f"  {n:<{name_w}} ({k}): {a}" for n, k, a in iface_rows)
    return lines


def _cmd_local() -> int:
    for line in _report_body(include_ipv6=True, egress4=net.egress_ip(socket.AF_INET)):
        print(line)
    return 0


def _cmd_run(wait: float, on_change: bool = False) -> int:
    cfg = load_config()
    egress4 = net.wait_for_network(timeout=wait)
    if egress4 is None:
        if on_change:
            # Normal for the periodic watcher: mark offline, report on recovery.
            prev = state.load_state()
            state.save_state(online=False, last_ip=prev.get("last_ip"))
            logger.info("offline; recorded, will report on recovery")
            return 0
        # No network after the wait: sending an all-unavailable email is
        # worse than not sending. Exit 1 so the autostart unit (Restart=
        # on-failure / KeepAlive) retries and reports once the net is up.
        logger.error("network not ready after %.0fs, not reporting", wait)
        return 1

    prev = state.load_state()
    if on_change:
        recovered = not prev.get("online", False)
        ip_changed = prev.get("last_ip") != egress4
        if not recovered and not ip_changed:
            state.save_state(online=True, last_ip=egress4)
            logger.info("network unchanged (%s), nothing to report", egress4)
            return 0

    body = _report_body(include_ipv6=cfg.report.include_ipv6, egress4=egress4)
    subject = f"[ip-report] {platform.node()} {egress4}"
    mail.send_report(cfg, subject, "\n".join(body))
    state.save_state(online=True, last_ip=egress4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
