"""Optional alerting: no-data (station/service silence), frost, high wind,
nearby lightning, rain starting, and low station battery.

Off unless a destination is configured (ALERT_WEBHOOK_URL and/or
ALERT_SMTP_HOST + ALERT_EMAIL_TO). When on, one evaluator thread watches the
in-memory caches every few seconds, and also drains a queue the UDP
listener pushes evt_strike / evt_precip / obs_st messages onto, so lightning
and rain alerts are instant when UDP is enabled. It never writes to the
caches or the observations table, so it can't interfere with collection.

No spam: each kind fires once when its condition begins and only re-arms
once the condition has cleared with some margin (temperature/wind/battery),
or after a quiet period (lightning/rain); a per-kind cooldown then caps how
often a re-armed kind can fire again. State lives in SQLite (alert_store)
so a restart doesn't re-send alerts for conditions that are still ongoing.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from . import alert_store
from .cache import LatestCache
from .notifier import (
    SMTP_SECURITY_MODES,
    WEBHOOK_FORMATS,
    Alert,
    EmailSender,
    Notifier,
    WebhookSender,
)

logger = logging.getLogger("weatherhub")

ALL_KINDS = ("no_data", "frost", "wind", "lightning", "rain", "battery")

CHECK_INTERVAL_SECONDS = 10.0
# Lightning/rain are events rather than levels: an episode stays active
# until this long passes without a new strike / sign of rain.
LIGHTNING_CLEAR_SECONDS = 30 * 60
RAIN_CLEAR_SECONDS = 60 * 60
BATTERY_MARGIN_VOLTS = 0.05

_KM_PER_MILE = 1.609344

_UNIT_DEFAULTS: dict[str, dict[str, Any]] = {
    "imperial": {
        "frost_temperature": 32.0,
        "frost_margin": 2.0,
        "wind_gust": 40.0,
        "wind_margin": 10.0,
        "lightning_distance": 10.0,
        "temp_unit": "°F",
        "speed_unit": "mph",
        "distance_unit": "mi",
    },
    "metric": {
        "frost_temperature": 0.0,
        "frost_margin": 1.0,
        "wind_gust": 18.0,
        "wind_margin": 4.5,
        "lightning_distance": 16.0,
        "temp_unit": "°C",
        "speed_unit": "m/s",
        "distance_unit": "km",
    },
}

# Cache keys that carry an observation timestamp. "time" is set by both
# sources; the *_updated_at keys are per-source copies of it.
_OBSERVATION_TIME_KEYS = ("time", "udp_updated_at", "rest_updated_at")

# obs_st "obs" array index of rain over the previous minute (mm), per v171.
_OBS_ST_RAIN_INDEX = 12


@dataclass
class AlertConfig:
    unit_system: str = "imperial"
    kinds: frozenset[str] = frozenset(ALL_KINDS)
    no_data_minutes: float = 15.0
    frost_temperature: float = 32.0
    wind_gust: float = 40.0
    lightning_distance: float = 10.0
    battery_voltage: float = 2.40
    cooldown_minutes: float = 60.0
    station_name: str = ""
    units: dict[str, Any] = field(default_factory=lambda: dict(_UNIT_DEFAULTS["imperial"]))


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def load_alert_config(env: Mapping[str, str], unit_system: str) -> AlertConfig:
    """Thresholds from ALERT_* env vars, in TEMPEST_UNITS' unit system.

    Raises ValueError on an invalid value.
    """
    units = dict(_UNIT_DEFAULTS.get(unit_system, _UNIT_DEFAULTS["imperial"]))

    raw_kinds = env.get("ALERT_KINDS", "").strip()
    if not raw_kinds or raw_kinds.lower() == "all":
        kinds = frozenset(ALL_KINDS)
    else:
        requested = {k.strip().lower() for k in raw_kinds.split(",") if k.strip()}
        unknown = requested - set(ALL_KINDS)
        if unknown:
            raise ValueError(
                f"ALERT_KINDS has unknown kind(s) {', '.join(sorted(unknown))}; "
                f"valid: {', '.join(ALL_KINDS)}"
            )
        kinds = frozenset(requested)

    config = AlertConfig(
        unit_system=unit_system if unit_system in _UNIT_DEFAULTS else "imperial",
        kinds=kinds,
        no_data_minutes=_env_float(env, "ALERT_NO_DATA_MINUTES", 15.0),
        frost_temperature=_env_float(env, "ALERT_FROST_TEMPERATURE", units["frost_temperature"]),
        wind_gust=_env_float(env, "ALERT_WIND_GUST", units["wind_gust"]),
        lightning_distance=_env_float(
            env, "ALERT_LIGHTNING_DISTANCE", units["lightning_distance"]
        ),
        battery_voltage=_env_float(env, "ALERT_BATTERY_VOLTAGE", 2.40),
        cooldown_minutes=_env_float(env, "ALERT_COOLDOWN_MINUTES", 60.0),
        station_name=env.get("ALERT_STATION_NAME", "").strip(),
        units=units,
    )
    if config.no_data_minutes <= 0:
        raise ValueError("ALERT_NO_DATA_MINUTES must be greater than 0")
    if config.cooldown_minutes < 0:
        raise ValueError("ALERT_COOLDOWN_MINUTES must not be negative")
    return config


def build_senders(env: Mapping[str, str]) -> list[Any]:
    """Destinations configured in env (possibly none). Raises ValueError on
    a half-configured or invalid destination."""
    senders: list[Any] = []

    url = env.get("ALERT_WEBHOOK_URL", "").strip()
    if url:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("ALERT_WEBHOOK_URL must start with http:// or https://")
        fmt = env.get("ALERT_WEBHOOK_FORMAT", "json").strip().lower() or "json"
        if fmt not in WEBHOOK_FORMATS:
            raise ValueError(f"ALERT_WEBHOOK_FORMAT must be one of {', '.join(WEBHOOK_FORMATS)}")
        senders.append(
            WebhookSender(url, fmt=fmt, token=env.get("ALERT_WEBHOOK_TOKEN", "").strip())
        )

    host = env.get("ALERT_SMTP_HOST", "").strip()
    if host:
        recipients = [
            addr.strip() for addr in env.get("ALERT_EMAIL_TO", "").split(",") if addr.strip()
        ]
        if not recipients:
            raise ValueError("ALERT_SMTP_HOST is set but ALERT_EMAIL_TO is empty")
        security = env.get("ALERT_SMTP_SECURITY", "starttls").strip().lower() or "starttls"
        if security not in SMTP_SECURITY_MODES:
            raise ValueError(
                f"ALERT_SMTP_SECURITY must be one of {', '.join(SMTP_SECURITY_MODES)}"
            )
        default_port = {"starttls": 587, "ssl": 465, "none": 25}[security]
        raw_port = env.get("ALERT_SMTP_PORT", "").strip()
        try:
            port = int(raw_port) if raw_port else default_port
        except ValueError as exc:
            raise ValueError(f"ALERT_SMTP_PORT must be an integer, got {raw_port!r}") from exc
        user = env.get("ALERT_SMTP_USER", "").strip()
        sender = env.get("ALERT_EMAIL_FROM", "").strip() or user
        if not sender:
            raise ValueError("ALERT_EMAIL_FROM (or ALERT_SMTP_USER) is required for email alerts")
        senders.append(
            EmailSender(
                host,
                port,
                security,
                sender,
                recipients,
                user=user,
                password=env.get("ALERT_SMTP_PASSWORD", ""),
            )
        )

    return senders


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fmt(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def _minutes(value: float) -> str:
    text = _fmt(value)
    return f"{text} minute" if text == "1" else f"{text} minutes"


def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(ts))


class AlertEngine:
    """Evaluates alert conditions. Not thread-safe: drive it from one thread
    (run_alerts_forever). emit(alert) is called for each alert raised, after
    it has been written to the alert log."""

    def __init__(
        self,
        config: AlertConfig,
        cache: LatestCache,
        health_cache: LatestCache,
        db_path: str,
        emit: Callable[[Alert], Any],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.cache = cache
        self.health_cache = health_cache
        self.db_path = db_path
        self._emit = emit
        self._clock = clock
        self.started_at = clock()

        persisted = alert_store.load_states(db_path)
        self.states: dict[str, dict[str, Any]] = {}
        for kind in ALL_KINDS:
            state = persisted.get(kind, {})
            self.states[kind] = {
                "active": bool(state.get("active")),
                "since": state.get("since"),
                "last_sent": state.get("last_sent"),
                "data": dict(state.get("data") or {}),
            }

        # Seeded from history so a no-data alert can say when the last
        # observation was, even right after a restart.
        self.last_obs_ts: float | None = alert_store.latest_observation_ts(db_path)
        self._prev_precip: tuple[float | None, float | None] | None = None

    # -- helpers -----------------------------------------------------------

    @property
    def _no_data_seconds(self) -> float:
        return self.config.no_data_minutes * 60

    def _station(self) -> str:
        if self.config.station_name:
            return self.config.station_name
        snapshot = self.cache.get() or {}
        return str(snapshot.get("location_name") or "Tempest station")

    def _save(self, kind: str) -> None:
        alert_store.save_state(self.db_path, kind, self.states[kind])

    def _send(self, kind: str, title: str, message: str, values: dict[str, Any], now: float) -> None:
        alert = Alert(
            kind=kind,
            title=title,
            message=message,
            station=self._station(),
            time=int(now),
            values=values,
        )
        try:
            alert.id = alert_store.log_alert(
                self.db_path, alert.time, kind, title, message, alert.station, values
            )
        except Exception:  # noqa: BLE001 - logging failure must not stop delivery
            logger.exception("Could not record %r alert in the alert log", kind)
        logger.info("Alert raised: %s - %s", title, message)
        self._emit(alert)

    def _begin(
        self,
        kind: str,
        now: float,
        title: str,
        message: str,
        values: dict[str, Any],
        data: dict[str, Any] | None = None,
        cooldown: bool = True,
    ) -> None:
        state = self.states[kind]
        state["active"] = True
        state["since"] = int(now)
        if data is not None:
            state["data"] = data
        last_sent = state.get("last_sent")
        cooldown_seconds = self.config.cooldown_minutes * 60
        if cooldown and last_sent is not None and now - last_sent < cooldown_seconds:
            logger.info(
                "Alert %r condition began but is in its cooldown - not sending", kind
            )
        else:
            state["last_sent"] = int(now)
            self._send(kind, title, message, values, now)
        self._save(kind)

    def _end(self, kind: str, now: float) -> None:
        state = self.states[kind]
        state["active"] = False
        state["since"] = int(now)
        self._save(kind)
        logger.info("Alert %r condition cleared - re-armed", kind)

    def _enabled(self, kind: str) -> bool:
        return kind in self.config.kinds

    # -- entry points ------------------------------------------------------

    def tick(self) -> None:
        """Evaluate every enabled kind against the current caches."""
        now = self._clock()
        snapshot = self.cache.get() or {}

        times = [t for t in (_num(snapshot.get(k)) for k in _OBSERVATION_TIME_KEYS) if t]
        if times:
            newest = max(times)
            if self.last_obs_ts is None or newest > self.last_obs_ts:
                self.last_obs_ts = newest

        fresh = self.last_obs_ts is not None and now - self.last_obs_ts <= self._no_data_seconds

        checks: list[tuple[str, Callable[[], None]]] = [
            ("no_data", lambda: self._check_no_data(now)),
            ("frost", lambda: self._check_frost(now, snapshot, fresh)),
            ("wind", lambda: self._check_wind(now, snapshot, fresh)),
            ("lightning", lambda: self._check_cloud_lightning(now, snapshot)),
            ("rain", lambda: self._check_cloud_rain(now, snapshot, fresh)),
            ("battery", lambda: self._check_battery(now)),
        ]
        for kind, check in checks:
            if not self._enabled(kind):
                continue
            try:
                check()
            except Exception:  # noqa: BLE001 - one bad check must not stop the rest
                logger.exception("Alert check %r failed", kind)

        for kind, window in (("lightning", LIGHTNING_CLEAR_SECONDS), ("rain", RAIN_CLEAR_SECONDS)):
            state = self.states[kind]
            last = _num(state["data"].get("last_evidence"))
            if state["active"] and (last is None or now - last > window):
                self._end(kind, now)

    def handle_event(self, message: dict[str, Any]) -> None:
        """A UDP message the listener forwarded (already hub-filtered)."""
        if not isinstance(message, dict):
            return
        now = self._clock()
        msg_type = message.get("type")

        if msg_type == "evt_strike" and self._enabled("lightning"):
            evt = message.get("evt")
            if isinstance(evt, list) and len(evt) >= 2:
                ts, distance_km = _num(evt[0]), _num(evt[1])
                if ts is not None and distance_km is not None:
                    distance = (
                        distance_km
                        if self.config.unit_system == "metric"
                        else distance_km / _KM_PER_MILE
                    )
                    self._lightning_evidence(now, ts, distance, "local UDP")

        elif msg_type == "evt_precip" and self._enabled("rain"):
            evt = message.get("evt")
            ts = _num(evt[0]) if isinstance(evt, list) and evt else None
            self._rain_evidence(now, ts if ts is not None else now, "local UDP")

        elif msg_type == "obs_st" and self._enabled("rain"):
            obs_list = message.get("obs")
            if isinstance(obs_list, list) and obs_list and isinstance(obs_list[0], list):
                obs = obs_list[0]
                if len(obs) > _OBS_ST_RAIN_INDEX:
                    rain = _num(obs[_OBS_ST_RAIN_INDEX])
                    ts = _num(obs[0])
                    if rain is not None and rain > 0:
                        self._rain_evidence(now, ts if ts is not None else now, "local UDP")

    # -- individual checks -------------------------------------------------

    def _check_no_data(self, now: float) -> None:
        state = self.states["no_data"]
        threshold = self._no_data_seconds
        last = self.last_obs_ts

        if not state["active"]:
            reference = max(last or 0, self.started_at)
            if now - reference < threshold:
                return
            if last is not None:
                ago = _minutes((now - last) // 60)
                detail = f"Last observation: {_fmt_time(last)} ({ago} ago)."
            else:
                detail = "No observation has been received since the service started."
            self._begin(
                "no_data",
                now,
                f"No weather data from {self._station()}",
                f"No new observation for at least {_minutes(self.config.no_data_minutes)}. "
                f"{detail}",
                {
                    "last_observation": int(last) if last is not None else None,
                    "threshold_minutes": self.config.no_data_minutes,
                },
                data={"last_obs": int(last) if last is not None else None},
                cooldown=False,
            )
            return

        stale = _num(state["data"].get("last_obs"))
        if last is not None and (stale is None or last > stale):
            self._end("no_data", now)
            values: dict[str, Any] = {"observation_time": int(last)}
            message = f"Observations resumed: newest observation at {_fmt_time(last)}."
            if stale is not None:
                gap_minutes = int((last - stale) // 60)
                values["gap_minutes"] = gap_minutes
                message += f" No data was received for {_minutes(gap_minutes)} before that."
            self._send("data_resumed", f"Weather data resumed for {self._station()}", message, values, now)

    def _check_frost(self, now: float, snapshot: dict[str, Any], fresh: bool) -> None:
        temp = _num(snapshot.get("air_temperature"))
        if temp is None or not fresh:
            return
        state = self.states["frost"]
        threshold = self.config.frost_temperature
        unit = self.config.units["temp_unit"]
        if not state["active"] and temp <= threshold:
            self._begin(
                "frost",
                now,
                f"Frost alert for {self._station()}",
                f"Temperature is {_fmt(temp)}{unit}, at or below the "
                f"{_fmt(threshold)}{unit} frost threshold.",
                {"air_temperature": temp, "threshold": threshold, "unit": unit},
            )
        elif state["active"] and temp > threshold + self.config.units["frost_margin"]:
            self._end("frost", now)

    def _check_wind(self, now: float, snapshot: dict[str, Any], fresh: bool) -> None:
        gust = _num(snapshot.get("wind_gust"))
        if gust is None or not fresh:
            return
        state = self.states["wind"]
        threshold = self.config.wind_gust
        unit = self.config.units["speed_unit"]
        if not state["active"] and gust >= threshold:
            avg = _num(snapshot.get("wind_avg"))
            avg_text = f" (average {_fmt(avg)} {unit})" if avg is not None else ""
            self._begin(
                "wind",
                now,
                f"High wind alert for {self._station()}",
                f"Wind gust of {_fmt(gust)} {unit}{avg_text}, at or above the "
                f"{_fmt(threshold)} {unit} threshold.",
                {"wind_gust": gust, "wind_avg": avg, "threshold": threshold, "unit": unit},
            )
        elif state["active"] and gust < threshold - self.config.units["wind_margin"]:
            self._end("wind", now)

    def _check_battery(self, now: float) -> None:
        health = self.health_cache.get() or {}
        volts = _num(health.get("device_voltage"))
        if volts is None:
            return
        state = self.states["battery"]
        threshold = self.config.battery_voltage
        if not state["active"] and volts <= threshold:
            self._begin(
                "battery",
                now,
                f"Low station battery at {self._station()}",
                f"Tempest battery is at {volts:.3f} V, at or below the {threshold:.2f} V "
                "threshold. The station reduces its reporting rate as the voltage drops.",
                {"device_voltage": volts, "threshold": threshold, "unit": "V"},
            )
        elif state["active"] and volts >= threshold + BATTERY_MARGIN_VOLTS:
            self._end("battery", now)

    def _check_cloud_lightning(self, now: float, snapshot: dict[str, Any]) -> None:
        epoch = _num(snapshot.get("lightning_strike_last_epoch"))
        distance = _num(snapshot.get("lightning_strike_last_distance"))
        if epoch is None or distance is None:
            return
        self._lightning_evidence(now, epoch, distance, "Tempest cloud")

    def _lightning_evidence(self, now: float, ts: float, distance: float, source: str) -> None:
        # Old strikes (e.g. the cloud's "last strike" from days ago, seen at
        # startup) are not evidence of a storm now.
        if now - ts > LIGHTNING_CLEAR_SECONDS:
            return
        threshold = self.config.lightning_distance
        if distance > threshold:
            return
        state = self.states["lightning"]
        previous = _num(state["data"].get("last_evidence"))
        if state["active"]:
            if previous is None or ts > previous:
                state["data"]["last_evidence"] = int(ts)
                self._save("lightning")
            return
        unit = self.config.units["distance_unit"]
        self._begin(
            "lightning",
            now,
            f"Lightning within {_fmt(threshold)} {unit} of {self._station()}",
            f"Lightning strike detected {_fmt(distance)} {unit} away at "
            f"{_fmt_time(ts)} (source: {source}).",
            {
                "distance": round(distance, 1),
                "threshold": threshold,
                "unit": unit,
                "strike_time": int(ts),
                "source": source,
            },
            data={"last_evidence": int(ts)},
        )

    def _check_cloud_rain(self, now: float, snapshot: dict[str, Any], fresh: bool) -> None:
        accum = _num(snapshot.get("precip_accum_local_day"))
        minutes = _num(snapshot.get("precip_minutes_local_day"))
        if accum is None and minutes is None:
            return
        previous = self._prev_precip
        self._prev_precip = (accum, minutes)
        if previous is None or not fresh:
            return  # need a baseline; a drop is the local-midnight reset
        increased = any(
            cur is not None and prev is not None and cur > prev
            for cur, prev in zip((accum, minutes), previous)
        )
        if increased:
            self._rain_evidence(now, now, "Tempest cloud")

    def _rain_evidence(self, now: float, ts: float, source: str) -> None:
        if now - ts > RAIN_CLEAR_SECONDS:
            return
        state = self.states["rain"]
        if state["active"]:
            previous = _num(state["data"].get("last_evidence"))
            if previous is None or ts > previous:
                state["data"]["last_evidence"] = int(ts)
                self._save("rain")
            return
        self._begin(
            "rain",
            now,
            f"Rain started at {self._station()}",
            f"Rain detected at {_fmt_time(ts)} (source: {source}).",
            {"start_time": int(ts), "source": source},
            data={"last_evidence": int(ts)},
        )


def run_alerts_forever(
    engine: AlertEngine,
    event_queue: queue.Queue[Any] | None,
    stop_event: threading.Event,
    interval_seconds: float = CHECK_INTERVAL_SECONDS,
) -> None:
    """Evaluator loop: tick every interval_seconds, and handle forwarded UDP
    events as soon as they arrive. Never raises."""
    next_tick = 0.0
    while not stop_event.is_set():
        now = time.monotonic()
        if now >= next_tick:
            next_tick = now + interval_seconds
            try:
                engine.tick()
            except Exception:  # noqa: BLE001 - keep the evaluator alive
                logger.exception("Alert evaluation failed")

        if event_queue is None:
            stop_event.wait(1.0)
            continue
        try:
            message = event_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            engine.handle_event(message)
        except Exception:  # noqa: BLE001
            logger.exception("Alert event handling failed")


@dataclass
class AlertService:
    engine: AlertEngine
    notifier: Notifier
    event_queue: queue.Queue[Any]
    thread: threading.Thread


def start_alerting(
    cache: LatestCache,
    health_cache: LatestCache,
    db_path: str,
    unit_system: str,
    stop_event: threading.Event,
    env: Mapping[str, str] | None = None,
    poll_interval_seconds: float | None = None,
    udp_enabled: bool = False,
) -> AlertService | None:
    """Start alerting if a destination is configured, else return None.

    A configuration error disables alerting (logged) rather than stopping
    the service - data collection always comes first.
    """
    env = os.environ if env is None else env
    try:
        senders = build_senders(env)
        if not senders:
            logger.info("Alerting off (no ALERT_WEBHOOK_URL or ALERT_SMTP_HOST configured)")
            return None
        config = load_alert_config(env, unit_system)
    except ValueError as exc:
        logger.error("Alerting disabled - invalid configuration: %s", exc)
        return None

    try:
        return _start(
            config, senders, cache, health_cache, db_path, stop_event,
            poll_interval_seconds, udp_enabled,
        )
    except Exception:  # noqa: BLE001 - never stop the service over alerting
        logger.exception("Alerting disabled - could not start")
        return None


def _start(
    config: AlertConfig,
    senders: list[Any],
    cache: LatestCache,
    health_cache: LatestCache,
    db_path: str,
    stop_event: threading.Event,
    poll_interval_seconds: float | None,
    udp_enabled: bool,
) -> AlertService:
    alert_store.init_alert_tables(db_path)

    if (
        "no_data" in config.kinds
        and not udp_enabled
        and poll_interval_seconds is not None
        and config.no_data_minutes * 60 < poll_interval_seconds + 120
    ):
        logger.warning(
            "ALERT_NO_DATA_MINUTES (%s) is close to or below POLL_INTERVAL_SECONDS (%s) "
            "with UDP off - expect false no-data alerts between polls",
            config.no_data_minutes,
            poll_interval_seconds,
        )

    def on_result(alert: Alert, status: str, detail: str) -> None:
        if alert.id is not None:
            try:
                alert_store.update_alert_status(db_path, alert.id, status, detail)
            except Exception:  # noqa: BLE001
                logger.exception("Could not record delivery status of alert %s", alert.id)

    notifier = Notifier(senders, on_result=on_result, stop_event=stop_event)
    engine = AlertEngine(config, cache, health_cache, db_path, notifier.submit)
    notifier.start()

    event_queue: queue.Queue[Any] = queue.Queue(maxsize=1000)
    thread = threading.Thread(
        target=run_alerts_forever,
        args=(engine, event_queue, stop_event),
        name="alert-evaluator",
        daemon=True,
    )
    thread.start()

    logger.info(
        "Alerting on: %s via %s",
        ", ".join(k for k in ALL_KINDS if k in config.kinds),
        "; ".join(sender.describe() for sender in senders),
    )
    return AlertService(engine, notifier, event_queue, thread)
