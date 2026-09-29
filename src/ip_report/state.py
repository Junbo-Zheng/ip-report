# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""Persistent last-report state (online flag + egress IP) for --on-change.

Lives next to the config (same per-OS user config dir). Missing or corrupt
state is treated as "first run" — the caller then always reports.
"""

import json
import logging
import os
from pathlib import Path

from ip_report.config import _user_config_dir

logger = logging.getLogger(__name__)


def _state_path() -> Path:
    return Path(os.path.join(_user_config_dir("ip-report"), "state.json"))


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.debug("no usable state file (%s); treating as first run", e)
        return {}


def save_state(online: bool, last_ip: str | None) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"online": online, "last_ip": last_ip}), encoding="utf-8"
    )
