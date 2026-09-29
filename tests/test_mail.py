# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

from ip_report import mail
from ip_report.config import Config, SmtpConfig


def _cfg(port=465):
    return Config(
        smtp=SmtpConfig(
            server="smtp.example.com",
            port=port,
            username="you@example.com",
            password="pw",
            from_addr="you@example.com",
            to=["a@example.com", "b@example.com"],
        )
    )


def test_build_message_headers():
    msg = mail.build_message(_cfg(), "subject here", "body text")
    assert msg["From"] == "ip-report <you@example.com>"
    assert msg["To"] == "a@example.com, b@example.com"
    assert "subject here" in str(msg["Subject"])
    # alternative multipart: plain part + monospaced HTML part.
    plain, html_part = msg.get_payload()
    assert plain.get_content_type() == "text/plain"
    assert "body text" in plain.get_payload(decode=True).decode()
    assert html_part.get_content_type() == "text/html"
    assert "<pre>body text</pre>" in html_part.get_payload(decode=True).decode()


def test_build_message_html_escapes_body():
    msg = mail.build_message(_cfg(), "s", "a<b>&c")
    html_part = msg.get_payload()[1]
    assert "&lt;b&gt;&amp;" in html_part.get_payload(decode=True).decode()


def test_send_report_ssl_port(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, server, port, context=None, **kw):
            sent["server"], sent["port"] = server, port
            sent["ssl"] = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, pw):
            sent["login"] = user

        def sendmail(self, from_addr, to, data):
            sent["mail"] = (from_addr, to, data)

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP)
    mail.send_report(_cfg(port=465), "s", "b")
    assert sent["ssl"] and sent["port"] == 465
    assert sent["login"] == "you@example.com"
    assert sent["mail"][1] == ["a@example.com", "b@example.com"]


def test_send_report_starttls_port(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, server, port, timeout=None):
            sent["ssl"] = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            sent["starttls"] = True

        def login(self, user, pw):
            pass

        def sendmail(self, from_addr, to, data):
            pass

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    mail.send_report(_cfg(port=587), "s", "b")
    assert sent["ssl"] is False
    assert sent.get("starttls") is True
