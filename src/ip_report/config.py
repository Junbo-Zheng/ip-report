# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""Load and validate the user's TOML config (SMTP credentials, report options)."""

import logging
import os
import sys
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:  # Python 3.11+
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib


class ConfigError(Exception):
    """Raised when the config file is missing, unreadable, or invalid."""


@dataclass
class SmtpConfig:
    server: str
    port: int
    username: str
    password: str
    from_addr: str
    to: list[str]


@dataclass
class ReportConfig:
    include_ipv6: bool = True


@dataclass
class Config:
    smtp: SmtpConfig
    report: ReportConfig = field(default_factory=ReportConfig)


def _user_config_dir(app: str) -> str:
    """Per-OS user config dir, matching platformdirs' user_config_dir layout."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", app)
    if os.name == "nt":
        base = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
        return os.path.join(base, app)
    base = os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config"))
    return os.path.join(base, app)


def config_path() -> str:
    return os.path.join(_user_config_dir("ip-report"), "config.toml")


# Template written by `ip-report init`. Every field carries a comment so a
# fresh install is self-explanatory — no example file needed, pip users
# don't have the repo.
CONFIG_TEMPLATE = """\
# ip-report configuration. Fill in your SMTP credentials, then run:
#   ip-report run          # send a one-shot report
#   ip-report install      # enable boot-time reporting

[smtp]
# Your mail provider's SMTP server. Port 465 = implicit SSL (recommended);
# any other port is treated as STARTTLS (e.g. 587).
server = "smtp.example.com"
port = 465
# SMTP login: account + password. For Gmail/QQ-style providers this is an
# app-specific password, NOT your mailbox password.
username = "you@example.com"
password = "app-password"
# Address the report is sent from (usually the same as username).
from_addr = "you@example.com"
# One or more recipients.
to = ["someone@example.com"]

[report]
# Include IPv6 addresses in the report body.
include_ipv6 = true
"""


def init_config() -> str:
    """Write the commented template to the user config path; returns the path."""
    path = config_path()
    if os.path.exists(path):
        raise ConfigError(f"config already exists at {path} — edit it directly")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(CONFIG_TEMPLATE)
    return path


def load_config(path: str | None = None) -> Config:
    path = path or config_path()
    if not os.path.isfile(path):
        raise ConfigError(
            f"config not found at {path}. Run 'ip-report init' to create a "
            "commented template there, then fill in your SMTP credentials "
            "(server, port, username, password, from_addr, to)."
        )
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"invalid TOML in {path}: {e}") from e

    try:
        smtp_raw = data["smtp"]
        smtp = SmtpConfig(
            server=smtp_raw["server"],
            port=int(smtp_raw["port"]),
            username=smtp_raw["username"],
            password=smtp_raw["password"],
            from_addr=smtp_raw["from_addr"],
            to=list(smtp_raw["to"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"missing or invalid [smtp] field in {path}: {e}") from e

    if not smtp.to:
        raise ConfigError(f"[smtp] to must list at least one recipient in {path}")

    report_raw = data.get("report", {})
    report = ReportConfig(
        include_ipv6=bool(report_raw.get("include_ipv6", True)),
    )
    logger.debug("loaded config from %s", path)
    return Config(smtp=smtp, report=report)
