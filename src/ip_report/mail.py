# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""Build and send the report email over SMTP (SSL on 465, STARTTLS otherwise)."""

import html
import logging
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from ip_report.config import Config

logger = logging.getLogger(__name__)

_SSL_PORT = 465


def build_message(cfg: Config, subject: str, body: str) -> MIMEMultipart:
    # Clients render text/plain bodies in a proportional font, which destroys
    # the space-aligned report. The HTML alternative wraps the same body in
    # <pre> so mail apps show it monospaced and aligned.
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr(("ip-report", cfg.smtp.from_addr))
    msg["To"] = ", ".join(cfg.smtp.to)
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(f"<pre>{html.escape(body)}</pre>", "html", "utf-8"))
    return msg


def send_report(cfg: Config, subject: str, body: str) -> None:
    msg = build_message(cfg, subject, body)
    if cfg.smtp.port == _SSL_PORT:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            cfg.smtp.server, cfg.smtp.port, context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(cfg.smtp.server, cfg.smtp.port, timeout=30)
        server.starttls(context=ssl.create_default_context())
    with server:
        server.login(cfg.smtp.username, cfg.smtp.password)
        server.sendmail(cfg.smtp.from_addr, cfg.smtp.to, msg.as_string())
    logger.info(
        "report sent to %s via %s:%d", cfg.smtp.to, cfg.smtp.server, cfg.smtp.port
    )
