import smtplib

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.graph.workflow import SEARCH_GRAPH
from app.jobs import assemble_result
from app.providers.demo import DemoAccommodationProvider, DemoFlightProvider
from app.services import mailer
from app.services.money import FxTable
from tests.test_workflow import deps_with, future_constraints


async def demo_package():
    fx = FxTable.fallback()
    deps = deps_with([DemoFlightProvider(fx)], [DemoAccommodationProvider()])
    c = future_constraints()
    st = await SEARCH_GRAPH.ainvoke({"constraints": c}, config={"configurable": {"deps": deps}})
    return assemble_result(st, c, deps, partial=False).packages[0]


def settings(**kw):
    base = {"gmail_address": "sender@gmail.com", "google_app_password": SecretStr("abcd efgh ijkl mnop")}
    base.update(kw)
    return Settings(**base)


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port
        self.logged_in = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        if password != "abcdefghijklmnop":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)


def test_configuration_detection():
    assert mailer.mail_configured(settings())
    assert not mailer.mail_configured(settings(google_app_password=None))
    assert not mailer.mail_configured(settings(gmail_address=""))
    assert mailer.app_password(settings()) == "abcdefghijklmnop"  # spaces from Google's display are removed


async def test_sends_html_with_attachments():
    pkg = await demo_package()
    await mailer.send_trip_email(settings(), "me@example.com", pkg, None, note="<script>x</script>")
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.gmail.com", 465)
    assert smtp.logged_in == ("sender@gmail.com", "abcdefghijklmnop")
    msg = smtp.sent[0]
    assert msg["To"] == "me@example.com" and pkg.destination_name in msg["Subject"]
    html = msg.get_body(("html",)).get_content()
    assert "&lt;script&gt;" in html and "<script>" not in html  # untrusted text is escaped
    names = [p.get_filename() for p in msg.iter_attachments()]
    assert any(n.endswith(".md") for n in names) and any(n.endswith(".json") for n in names)
    assert "abcdefghijklmnop" not in msg.as_string()


async def test_rejects_bad_recipient_and_missing_config():
    pkg = await demo_package()
    with pytest.raises(mailer.MailError):
        await mailer.send_trip_email(settings(), "not-an-email", pkg, None)
    with pytest.raises(mailer.MailError, match="GOOGLE_APP_PASSWORD"):
        await mailer.send_trip_email(settings(google_app_password=None), "me@example.com", pkg, None)
    assert FakeSMTP.instances == []


async def test_auth_failure_gives_clear_message():
    pkg = await demo_package()
    with pytest.raises(mailer.MailError, match="odbio prijavu"):
        await mailer.send_trip_email(settings(google_app_password=SecretStr("wrong")), "me@example.com", pkg, None)
