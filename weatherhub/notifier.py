"""Alert delivery: pluggable senders (webhook, email) and a background
delivery worker with bounded retries.

Nothing here may hold up data collection: alerts are handed to Notifier,
which queues them (bounded, never blocking the caller) for a single worker
thread to deliver. Every network call has a timeout, failed deliveries are
retried a bounded number of times and then logged and dropped.

Secrets stay out of the logs: webhook URLs usually embed a token, so they're
only ever logged as scheme://host/..., and the SMTP password is never logged.
"""

from __future__ import annotations

import json
import logging
import queue
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Callable, Protocol

logger = logging.getLogger("weatherhub")

USER_AGENT = "tempest-weather-hub (+https://github.com/dhiorns15/tempest-weather-hub)"

WEBHOOK_FORMATS = ("json", "ntfy")
SMTP_SECURITY_MODES = ("starttls", "ssl", "none")

# Discord rejects message content longer than this.
_DISCORD_CONTENT_LIMIT = 2000

# ntfy priorities (1 = min ... 5 = max) and tags (emoji short codes) per kind.
_NTFY_PRIORITY = {
    "no_data": 4,
    "frost": 4,
    "wind": 4,
    "lightning": 4,
    "battery": 3,
    "rain": 3,
    "data_resumed": 3,
    "test": 3,
}
_NTFY_TAGS = {
    "no_data": "warning",
    "data_resumed": "white_check_mark",
    "frost": "snowflake",
    "wind": "dash",
    "lightning": "zap",
    "rain": "umbrella",
    "battery": "battery",
    "test": "bell",
}


@dataclass
class Alert:
    kind: str
    title: str
    message: str
    station: str
    time: int
    values: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    def text(self) -> str:
        return f"{self.title}\n{self.message}"


class DeliveryError(Exception):
    """A send failed. retryable=False means trying again won't help."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Sender(Protocol):
    name: str

    def describe(self) -> str: ...

    def send(self, alert: Alert) -> None: ...


def redact_url(url: str) -> str:
    """scheme://host/... - enough to tell destinations apart in logs without
    leaking the token most webhook URLs carry in their path or query."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or "?"
    if parts.port:
        host = f"{host}:{parts.port}"
    return f"{parts.scheme or '?'}://{host}/..."


def _header_safe(value: str) -> str:
    """HTTP header values are Latin-1 on the wire; keep them plain ASCII."""
    cleaned = value.replace("\r", " ").replace("\n", " ").replace("°", " deg ")
    return " ".join(cleaned.encode("ascii", "replace").decode("ascii").split())


def json_payload(alert: Alert) -> dict[str, Any]:
    """Default webhook body: `text` is what Slack incoming webhooks read,
    `content` what Discord webhooks read; the structured fields are for
    anything else (Home Assistant, n8n, a custom receiver, ...)."""
    text = alert.text()
    return {
        "text": text,
        "content": text[:_DISCORD_CONTENT_LIMIT],
        "kind": alert.kind,
        "title": alert.title,
        "message": alert.message,
        "station": alert.station,
        "time": alert.time,
        "values": alert.values,
    }


class WebhookSender:
    """HTTP POST to a URL, either as JSON (Slack/Discord/generic) or in
    ntfy's plain-text-body-plus-headers format."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        fmt: str = "json",
        token: str = "",
        timeout: float = 10.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if fmt not in WEBHOOK_FORMATS:
            raise ValueError(f"webhook format must be one of {', '.join(WEBHOOK_FORMATS)}")
        self.url = url
        self.fmt = fmt
        self.token = token
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    def describe(self) -> str:
        return f"webhook ({self.fmt}) {redact_url(self.url)}"

    def build_request(self, alert: Alert) -> urllib.request.Request:
        headers = {"User-Agent": USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        if self.fmt == "ntfy":
            body = alert.message.encode("utf-8")
            headers["Content-Type"] = "text/plain; charset=utf-8"
            headers["Title"] = _header_safe(f"{alert.title}")
            headers["Priority"] = str(_NTFY_PRIORITY.get(alert.kind, 3))
            tag = _NTFY_TAGS.get(alert.kind)
            if tag:
                headers["Tags"] = tag
        else:
            body = json.dumps(json_payload(alert)).encode("utf-8")
            headers["Content-Type"] = "application/json"

        return urllib.request.Request(self.url, data=body, headers=headers, method="POST")

    def send(self, alert: Alert) -> None:
        request = self.build_request(alert)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                response.read(1024)
        except urllib.error.HTTPError as exc:
            # 408/429/5xx are worth retrying; any other 4xx means the request
            # itself is wrong (bad URL/token/body) and will fail again.
            retryable = exc.code in (408, 429) or exc.code >= 500
            raise DeliveryError(f"HTTP {exc.code}", retryable=retryable) from exc
        except urllib.error.URLError as exc:
            raise DeliveryError(f"connection failed: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise DeliveryError(f"{type(exc).__name__}: {exc}") from exc


class EmailSender:
    """Plain-text email over SMTP (STARTTLS, implicit TLS, or plain)."""

    name = "email"

    def __init__(
        self,
        host: str,
        port: int,
        security: str,
        sender: str,
        recipients: list[str],
        user: str = "",
        password: str = "",
        timeout: float = 15.0,
        smtp_class: type[smtplib.SMTP] = smtplib.SMTP,
        smtp_ssl_class: type[smtplib.SMTP_SSL] = smtplib.SMTP_SSL,
    ) -> None:
        if security not in SMTP_SECURITY_MODES:
            raise ValueError(f"SMTP security must be one of {', '.join(SMTP_SECURITY_MODES)}")
        self.host = host
        self.port = port
        self.security = security
        self.sender = sender
        self.recipients = recipients
        self.user = user
        self._password = password
        self.timeout = timeout
        self._smtp_class = smtp_class
        self._smtp_ssl_class = smtp_ssl_class

    def describe(self) -> str:
        return (
            f"email via {self.host}:{self.port} ({self.security}) "
            f"to {len(self.recipients)} recipient(s)"
        )

    def build_message(self, alert: Alert) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = alert.title
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg["Date"] = formatdate(alert.time, localtime=True)
        msg["Message-ID"] = make_msgid(domain="tempest-weather-hub")
        lines = [alert.message, "", f"Station: {alert.station}", f"Alert type: {alert.kind}"]
        if alert.values:
            lines.append("")
            lines.extend(f"{key}: {value}" for key, value in alert.values.items())
        # Quoted-printable keeps the body 7-bit (e.g. the degree sign), so it
        # goes through servers that don't advertise 8BITMIME.
        msg.set_content("\n".join(lines) + "\n", cte="quoted-printable")
        return msg

    def send(self, alert: Alert) -> None:
        msg = self.build_message(alert)
        context = ssl.create_default_context()
        try:
            if self.security == "ssl":
                smtp = self._smtp_ssl_class(
                    self.host, self.port, timeout=self.timeout, context=context
                )
            else:
                smtp = self._smtp_class(self.host, self.port, timeout=self.timeout)
            with smtp:
                if self.security == "starttls":
                    smtp.starttls(context=context)
                if self.user:
                    smtp.login(self.user, self._password)
                smtp.send_message(msg, from_addr=self.sender, to_addrs=self.recipients)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPNotSupportedError) as exc:
            raise DeliveryError(self._scrub(f"{type(exc).__name__}: {exc}"), retryable=False) from exc
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            raise DeliveryError(self._scrub(f"{type(exc).__name__}: {exc}"), retryable=False) from exc
        except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
            raise DeliveryError(self._scrub(f"{type(exc).__name__}: {exc}")) from exc

    def _scrub(self, text: str) -> str:
        if self._password:
            text = text.replace(self._password, "***")
        return text


def _scrub_url(text: str, sender: Any) -> str:
    url = getattr(sender, "url", "")
    if url and url in text:
        text = text.replace(url, redact_url(url))
    return text


def deliver(
    alert: Alert,
    senders: list[Any],
    retry_delays: tuple[float, ...] = (10.0, 60.0),
    sleep: Callable[[float], Any] = time.sleep,
) -> tuple[str, str]:
    """Send alert through every sender, retrying each failure up to
    len(retry_delays) more times. Never raises.

    Returns (status, detail): status is "sent" (all succeeded), "failed"
    (none did) or "partial"; detail summarises each sender's outcome.
    """
    outcomes = []
    succeeded = 0
    for sender in senders:
        attempts = 1 + len(retry_delays)
        error = ""
        for attempt in range(attempts):
            try:
                sender.send(alert)
                error = ""
                break
            except DeliveryError as exc:
                error = _scrub_url(str(exc), sender)
                retry = exc.retryable and attempt + 1 < attempts
            except Exception as exc:  # noqa: BLE001 - a sender bug must not kill delivery
                error = _scrub_url(f"{type(exc).__name__}: {exc}", sender)
                retry = attempt + 1 < attempts
            logger.warning(
                "Alert %r delivery via %s failed (attempt %d/%d): %s",
                alert.kind,
                sender.describe(),
                attempt + 1,
                attempts,
                error,
            )
            if not retry:
                break
            sleep(retry_delays[attempt])

        if error:
            outcomes.append(f"{sender.name}: failed ({error})")
            logger.error("Giving up on alert %r via %s", alert.kind, sender.describe())
        else:
            succeeded += 1
            outcomes.append(f"{sender.name}: sent")
            logger.info("Alert %r sent via %s", alert.kind, sender.describe())

    if succeeded == len(senders):
        status = "sent"
    elif succeeded == 0:
        status = "failed"
    else:
        status = "partial"
    return status, "; ".join(outcomes)


class Notifier:
    """Queues alerts for a single background delivery thread.

    submit() never blocks: if the queue is full (destinations down for a
    long time while alerts keep coming) the alert is logged and dropped.
    on_result(alert, status, detail) is called after each delivery.
    """

    def __init__(
        self,
        senders: list[Any],
        on_result: Callable[[Alert, str, str], None] | None = None,
        retry_delays: tuple[float, ...] = (10.0, 60.0),
        stop_event: threading.Event | None = None,
        maxsize: int = 100,
    ) -> None:
        self.senders = senders
        self._on_result = on_result
        self._retry_delays = retry_delays
        self._stop_event = stop_event or threading.Event()
        self._queue: queue.Queue[Alert | None] = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="alert-notifier", daemon=True)
        self._thread.start()

    def submit(self, alert: Alert) -> bool:
        try:
            self._queue.put_nowait(alert)
            return True
        except queue.Full:
            logger.error("Alert queue full - dropping %r alert", alert.kind)
            return False

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            alert = self._queue.get()
            if alert is None:
                return
            try:
                status, detail = deliver(
                    alert, self.senders, self._retry_delays, self._stop_event.wait
                )
                if self._on_result is not None:
                    self._on_result(alert, status, detail)
            except Exception:  # noqa: BLE001 - keep the worker alive
                logger.exception("Unexpected error delivering %r alert", alert.kind)
