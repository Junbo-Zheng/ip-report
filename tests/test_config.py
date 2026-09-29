# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

import pytest

from ip_report.config import ConfigError, load_config

VALID = """
[smtp]
server = "smtp.example.com"
port = 465
username = "you@example.com"
password = "app-password"
from_addr = "you@example.com"
to = ["someone@example.com"]

[report]
include_ipv6 = false
"""


def _write(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(content)
    return str(path)


def test_load_valid(tmp_path):
    cfg = load_config(_write(tmp_path, VALID))
    assert cfg.smtp.server == "smtp.example.com"
    assert cfg.smtp.port == 465
    assert cfg.smtp.to == ["someone@example.com"]
    assert cfg.report.include_ipv6 is False


def test_defaults_without_report_section(tmp_path):
    cfg = load_config(_write(tmp_path, VALID.split("[report]")[0]))
    assert cfg.report.include_ipv6 is True


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="config not found"):
        load_config(str(tmp_path / "nope.toml"))


def test_missing_file_error_mentions_init(tmp_path):
    with pytest.raises(ConfigError, match=r"ip-report init"):
        load_config(str(tmp_path / "nope.toml"))


def test_init_writes_loadable_template(tmp_path, monkeypatch):
    from ip_report import config

    monkeypatch.setattr(config, "config_path", lambda: str(tmp_path / "config.toml"))
    path = config.init_config()
    assert path == str(tmp_path / "config.toml")
    # The template must parse and carry field comments.
    content = open(path).read()
    assert "app-specific password" in content
    # The template must be well-formed: it loads as-is with placeholder values.
    cfg = config.load_config(path)
    assert cfg.smtp.server == "smtp.example.com"


def test_init_refuses_overwrite(tmp_path, monkeypatch):
    from ip_report import config

    monkeypatch.setattr(config, "config_path", lambda: str(tmp_path / "config.toml"))
    config.init_config()
    with pytest.raises(ConfigError, match="already exists"):
        config.init_config()


def test_missing_smtp_field(tmp_path):
    bad = VALID.replace('password = "app-password"\n', "")
    with pytest.raises(ConfigError, match=r"\[smtp\]"):
        load_config(_write(tmp_path, bad))


def test_empty_recipients(tmp_path):
    bad = VALID.replace('to = ["someone@example.com"]', "to = []")
    with pytest.raises(ConfigError, match="at least one recipient"):
        load_config(_write(tmp_path, bad))


def test_invalid_toml(tmp_path):
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(_write(tmp_path, "not [ valid toml"))
