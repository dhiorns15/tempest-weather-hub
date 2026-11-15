import io
import json
import smtplib
import threading
import unittest
import urllib.error

from weatherhub.notifier import (
    Alert,
    DeliveryError,
    EmailSender,
    Notifier,
    WebhookSender,
    deliver,
    redact_url,
)

SECRET_URL = "https://discord.example/api/webhooks/123/s3cr3t-token"


def make_alert(kind: str = "frost") -> Alert:
    return Alert(
        kind=kind,
        title="Frost alert for Tower 1",
        message="Temperature is 31.5°F, at or below the 32°F frost threshold.",
        station="Tower 1",
        time=1_800_000_000,
        values={"air_temperature": 31.5, "threshold": 32.0, "unit": "°F"},
        id=7,
    )


class FakeResponse:
    def __init__(self) -> None:
        self.status = 200

    def read(self, n: int = -1) -> bytes:
        return b"ok"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class RecordingOpener:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.requests = []
        self.timeouts = []
        self.failures = list(failures or [])

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.failures:
            raise self.failures.pop(0)
        return FakeResponse()


class TestWebhookSender(unittest.TestCase):
    def test_json_body_works_for_slack_and_discord(self) -> None:
        opener = RecordingOpener()
        WebhookSender(SECRET_URL, opener=opener).send(make_alert())

        request = opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, SECRET_URL)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertIn("tempest-weather-hub", request.get_header("User-agent"))
        self.assertEqual(opener.timeouts, [10.0])

        body = json.loads(request.data)
        expected_text = "Frost alert for Tower 1\n" + make_alert().message
        self.assertEqual(body["text"], expected_text)  # Slack
        self.assertEqual(body["content"], expected_text)  # Discord
        self.assertEqual(body["kind"], "frost")
        self.assertEqual(body["title"], "Frost alert for Tower 1")
        self.assertEqual(body["station"], "Tower 1")
        self.assertEqual(body["time"], 1_800_000_000)
        self.assertEqual(body["values"]["air_temperature"], 31.5)

    def test_discord_content_is_capped(self) -> None:
        alert = make_alert()
        alert.message = "x" * 5000
        opener = RecordingOpener()
        WebhookSender(SECRET_URL, opener=opener).send(alert)
        body = json.loads(opener.requests[0].data)
        self.assertEqual(len(body["content"]), 2000)
        self.assertGreater(len(body["text"]), 2000)

    def test_ntfy_format(self) -> None:
        opener = RecordingOpener()
        WebhookSender("https://ntfy.example/weather", fmt="ntfy", token="tk_abc", opener=opener).send(
            make_alert("no_data")
        )
        request = opener.requests[0]
        self.assertEqual(request.data.decode("utf-8"), make_alert().message)
        self.assertEqual(request.get_header("Title"), "Frost alert for Tower 1")
        self.assertEqual(request.get_header("Priority"), "4")
        self.assertEqual(request.get_header("Tags"), "warning")
        self.assertEqual(request.get_header("Authorization"), "Bearer tk_abc")
        self.assertTrue(request.get_header("Content-type").startswith("text/plain"))

    def test_ntfy_title_is_ascii_safe(self) -> None:
        alert = make_alert()
        alert.title = "Frost at Café\r\nX-Injected: 1 - 31°F"
        opener = RecordingOpener()
        WebhookSender("https://ntfy.example/weather", fmt="ntfy", opener=opener).send(alert)
        title = opener.requests[0].get_header("Title")
        title.encode("ascii")
        self.assertNotIn("\n", title)
        self.assertNotIn("\r", title)

    def test_http_errors_are_classified(self) -> None:
        def http_error(code: int) -> urllib.error.HTTPError:
            return urllib.error.HTTPError(SECRET_URL, code, "err", {}, io.BytesIO(b""))

        for code, retryable in ((400, False), (404, False), (429, True), (503, True)):
            sender = WebhookSender(SECRET_URL, opener=RecordingOpener([http_error(code)]))
            with self.assertRaises(DeliveryError) as ctx:
                sender.send(make_alert())
            self.assertEqual(ctx.exception.retryable, retryable, code)

        sender = WebhookSender(
            SECRET_URL, opener=RecordingOpener([urllib.error.URLError("refused")])
        )
        with self.assertRaises(DeliveryError) as ctx:
            sender.send(make_alert())
        self.assertTrue(ctx.exception.retryable)

    def test_describe_hides_the_secret(self) -> None:
        sender = WebhookSender(SECRET_URL)
        self.assertNotIn("s3cr3t", sender.describe())
        self.assertEqual(redact_url(SECRET_URL), "https://discord.example/...")
        self.assertEqual(redact_url("http://10.0.0.5:8123/api/webhook/x"), "http://10.0.0.5:8123/...")


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None) -> None:
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials for " + password.encode())

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.calls.append("send")
        self.sent.append((msg, from_addr, to_addrs))


class TestEmailSender(unittest.TestCase):
    def setUp(self) -> None:
        FakeSMTP.instances = []
        FakeSMTP.fail_login = False

    def sender(self, security: str = "starttls", **kwargs) -> EmailSender:
        return EmailSender(
            "mail.example",
            587,
            security,
            "hub@example.com",
            ["me@example.com", "you@example.com"],
            smtp_class=FakeSMTP,
            smtp_ssl_class=FakeSMTP,
            **kwargs,
        )

    def test_starttls_login_and_message(self) -> None:
        self.sender(user="hub@example.com", password="pw").send(make_alert())
        smtp = FakeSMTP.instances[0]
        self.assertEqual(smtp.calls, ["starttls", ("login", "hub@example.com"), "send", "quit"])
        self.assertEqual(smtp.timeout, 15.0)
        msg, from_addr, to_addrs = smtp.sent[0]
        self.assertEqual(msg["Subject"], "Frost alert for Tower 1")
        self.assertEqual(msg["To"], "me@example.com, you@example.com")
        self.assertEqual(from_addr, "hub@example.com")
        self.assertEqual(to_addrs, ["me@example.com", "you@example.com"])
        body = msg.get_content()
        self.assertIn("31.5°F", body)
        self.assertIn("Station: Tower 1", body)

    def test_ssl_and_plain_without_login(self) -> None:
        self.sender("ssl").send(make_alert())
        self.assertIsNotNone(FakeSMTP.instances[0].context)
        self.assertEqual(FakeSMTP.instances[0].calls, ["send", "quit"])

        self.sender("none").send(make_alert())
        self.assertEqual(FakeSMTP.instances[1].calls, ["send", "quit"])

    def test_auth_failure_is_not_retryable_and_password_is_scrubbed(self) -> None:
        FakeSMTP.fail_login = True
        with self.assertRaises(DeliveryError) as ctx:
            self.sender(user="u", password="hunter2").send(make_alert())
        self.assertFalse(ctx.exception.retryable)
        self.assertNotIn("hunter2", str(ctx.exception))

    def test_describe_has_no_password(self) -> None:
        description = self.sender(user="u", password="hunter2").describe()
        self.assertNotIn("hunter2", description)


class FlakySender:
    name = "flaky"

    def __init__(self, failures: list[Exception]) -> None:
        self.failures = list(failures)
        self.attempts = 0

    def describe(self) -> str:
        return "flaky test sender"

    def send(self, alert: Alert) -> None:
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)


class TestDeliver(unittest.TestCase):
    def test_retries_then_succeeds(self) -> None:
        sleeps = []
        sender = FlakySender([DeliveryError("down"), DeliveryError("down")])
        with self.assertLogs("weatherhub", level="WARNING"):
            status, detail = deliver(make_alert(), [sender], (1.0, 2.0), sleeps.append)
        self.assertEqual((status, detail), ("sent", "flaky: sent"))
        self.assertEqual(sender.attempts, 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_gives_up_after_bounded_attempts(self) -> None:
        sleeps = []
        sender = FlakySender([DeliveryError("down")] * 10)
        with self.assertLogs("weatherhub", level="ERROR"):
            status, _ = deliver(make_alert(), [sender], (1.0, 2.0), sleeps.append)
        self.assertEqual(status, "failed")
        self.assertEqual(sender.attempts, 3)

    def test_no_retry_for_permanent_errors(self) -> None:
        sender = FlakySender([DeliveryError("HTTP 404", retryable=False)])
        with self.assertLogs("weatherhub", level="ERROR"):
            status, _ = deliver(make_alert(), [sender], (1.0,), lambda s: None)
        self.assertEqual(status, "failed")
        self.assertEqual(sender.attempts, 1)

    def test_partial_and_unexpected_exceptions(self) -> None:
        bad = FlakySender([RuntimeError("bug")] * 5)
        good = FlakySender([])
        with self.assertLogs("weatherhub", level="WARNING"):
            status, detail = deliver(make_alert(), [bad, good], (0.0,), lambda s: None)
        self.assertEqual(status, "partial")
        self.assertIn("flaky: failed (RuntimeError: bug)", detail)

    def test_logs_never_contain_the_webhook_secret(self) -> None:
        error = urllib.error.URLError(f"cannot reach {SECRET_URL}")
        sender = WebhookSender(SECRET_URL, opener=RecordingOpener([error]))
        with self.assertLogs("weatherhub", level="WARNING") as logs:
            status, detail = deliver(make_alert(), [sender], (), lambda s: None)
        self.assertEqual(status, "failed")
        self.assertNotIn("s3cr3t", detail)
        self.assertNotIn("s3cr3t", "\n".join(logs.output))


class TestNotifier(unittest.TestCase):
    def test_delivers_in_background_and_reports(self) -> None:
        results = []
        done = threading.Event()

        def on_result(alert, status, detail):
            results.append((alert.id, status, detail))
            done.set()

        notifier = Notifier([FlakySender([])], on_result=on_result, retry_delays=())
        notifier.start()
        with self.assertLogs("weatherhub", level="INFO"):
            self.assertTrue(notifier.submit(make_alert()))
            self.assertTrue(done.wait(5))
        notifier.stop()
        notifier.join(5)
        self.assertEqual(results, [(7, "sent", "flaky: sent")])

    def test_submit_never_blocks_when_full(self) -> None:
        notifier = Notifier([FlakySender([])], maxsize=1)  # not started: nothing drains
        self.assertTrue(notifier.submit(make_alert()))
        with self.assertLogs("weatherhub", level="ERROR"):
            self.assertFalse(notifier.submit(make_alert()))


if __name__ == "__main__":
    unittest.main()
