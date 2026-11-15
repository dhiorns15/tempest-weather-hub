# Tempest Weather Hub

A standalone, dependency-free Python service and website for a
[Tempest](https://tempestwx.com) (WeatherFlow) personal weather station:
current conditions, hourly/daily forecast, a permanent SQLite history with
per-metric charts, an admin-keyed history API, embeddable widgets,
Kiosk-compatible output, and station health monitoring — all from either
Tempest's cloud API, its local UDP broadcast, or both.

## How it works

Two independent, optional data sources feed the same in-memory caches and
SQLite history — **only one is required, not both** (see
[Running with only one data source](#running-with-only-one-data-source)):

- **Tempest cloud (REST)** — a background thread polls the `better_forecast`
  endpoint every `POLL_INTERVAL_SECONDS` (5 minutes by default). This is the
  only source for forecast, `conditions`/`icon` text, station name, and
  several metrics (precipitation detail, lightning, comfort fields) that
  aren't in the local protocol at all.
- **Local UDP broadcast** (optional) — the station's hub broadcasts raw
  observations on the local network roughly once a minute; enabling this
  (`TEMPEST_UDP_ENABLED=true`) gives faster current-conditions updates and
  its own history writes, and also powers the station-health panel.

The site/API never block on a live Tempest call — both sources only ever
update an in-memory cache, and if a poll/broadcast fails, the last-known-good
snapshot keeps being served.

WeatherFlow doesn't publish a numeric REST rate limit for personal access
tokens (only "enough for personal use"), but there's no real benefit to
polling faster than the 5-minute default anyway — the station itself only
produces a new observation about once a minute, and the forecast model
doesn't regenerate that quickly either.

## Configuration (environment variables)

**At least one data source is required, not both**: Tempest cloud (REST -
`TEMPEST_STATION_ID` + `TEMPEST_TOKEN`) and/or the local UDP broadcast
(`TEMPEST_UDP_ENABLED=true`). Running with just one works fine and the site
adapts automatically - see [Running with only one data source](#running-with-only-one-data-source) below.

| Variable | Required | Default | Description |
|---|---|---|---|
| `TEMPEST_STATION_ID` | One of this pair, or UDP | — | Your Tempest station ID |
| `TEMPEST_TOKEN` | One of this pair, or UDP | — | Tempest personal access token — generate one at [tempestwx.com/settings/tokens](https://tempestwx.com/settings/tokens) |
| `TEMPEST_UNITS` | No | `imperial` | `metric` (°C, m/s, mb, mm, km) or `imperial` (°F, mph, inHg, in, mi); any other value is treated as `imperial`. The site and widget label everything to match. History is stored in whatever units were active when each row was written and is not converted if you switch later |
| `PORT` | No | `8080` | HTTP port to listen on |
| `POLL_INTERVAL_SECONDS` | No | `300` | Seconds between Tempest polls |
| `DB_PATH` | No | `data/weather.db` | Path to the SQLite history file |
| `STATIC_DIR` | No | `static` | Path to the static site directory |
| `TEMPEST_UDP_ENABLED` | One of this, or the cloud pair | `false` | `true` to listen for the station's local UDP broadcast (see below) |
| `TEMPEST_UDP_PORT` | No | `50222` | UDP port to listen on - Tempest hubs always broadcast to 50222, so there's rarely a reason to change this |
| `TEMPEST_HUB_SERIAL` | No | — | Optional `hub_sn` filter (e.g. `HB-00118618`); blank accepts any hub heard |
| `BACKFILL_ON_STARTUP` | No | `true` | `false` to stop the service from filling history it missed while it was down (cloud source only - see [Backfilling missing history](#backfilling-missing-history)) |
| `BACKFILL_MAX_DAYS` | No | `30` | How far back the startup catch-up reaches |
| `STATION_TIMEZONE` | No | cloud-reported, else `UTC` | IANA timezone name (e.g. `America/Denver`) whose calendar days the daily history buckets follow. Normally unnecessary: the timezone Tempest's cloud reports for the station is used and remembered across restarts. Set it for UDP-only setups, or to override. An invalid name is logged and ignored |
| `ALERT_WEBHOOK_URL` | No | — | Alert webhook (Slack, Discord, ntfy, or anything taking a POST) - see [Alerts](#alerts-optional) |
| `ALERT_WEBHOOK_FORMAT` | No | `json` | `json` (Slack/Discord/generic) or `ntfy` |
| `ALERT_WEBHOOK_TOKEN` | No | — | Sent as `Authorization: Bearer ...` (e.g. an ntfy access token) |
| `ALERT_SMTP_HOST` | No | — | SMTP server for email alerts (also needs `ALERT_EMAIL_TO`) |
| `ALERT_SMTP_PORT` | No | `587` / `465` / `25` | Defaults by `ALERT_SMTP_SECURITY` |
| `ALERT_SMTP_SECURITY` | No | `starttls` | `starttls`, `ssl` (implicit TLS), or `none` |
| `ALERT_SMTP_USER` / `ALERT_SMTP_PASSWORD` | No | — | SMTP login, if the server needs one |
| `ALERT_EMAIL_FROM` | No | `ALERT_SMTP_USER` | Sender address |
| `ALERT_EMAIL_TO` | With SMTP | — | Recipient(s), comma-separated |
| `ALERT_KINDS` | No | all | Comma-separated subset of `no_data,frost,wind,lightning,rain,battery` |
| `ALERT_NO_DATA_MINUTES` | No | `15` | Minutes without a new observation before a no-data alert |
| `ALERT_FROST_TEMPERATURE` | No | `32` °F / `0` °C | Frost alert at or below this temperature |
| `ALERT_WIND_GUST` | No | `40` mph / `18` m/s | High-wind alert at or above this gust |
| `ALERT_LIGHTNING_DISTANCE` | No | `10` mi / `16` km | Lightning alert for a strike this close or closer |
| `ALERT_BATTERY_VOLTAGE` | No | `2.40` | Low-battery alert at or below this voltage (UDP only) |
| `ALERT_COOLDOWN_MINUTES` | No | `60` | Minimum time between two alerts of the same kind (not applied to no-data) |
| `ALERT_STATION_NAME` | No | station name from Tempest | Name used in alert text |

## Running locally

```bash
TEMPEST_STATION_ID=104000 TEMPEST_TOKEN=your-token python main.py
curl http://localhost:8080/api/current
```

...or copy `.env.example` to `.env`, fill in your values, and just run
`python main.py` — a `.env` file in the working directory is loaded
automatically (real environment variables still take precedence over it).

Then open http://localhost:8080/ for the site.

## Running the tests

```bash
python -m unittest discover -s tests -v
```

## Running with Docker Compose

The included `docker-compose.yaml` builds straight from this GitHub repo — no
local clone needed. Save `docker-compose.yaml` and `.env.example` somewhere,
copy `.env.example` to `.env`, fill in your values, then:

```bash
docker compose up -d
```

The service sets `pull_policy: build`, so every `up` rebuilds the image, and
Docker resolves `main` to its current commit before building — running `up`
again is how you update to whatever's latest on GitHub. If nothing's changed
upstream, the rebuild is a fast no-op. To start the image already built
locally without rebuilding, add `--no-build`.

`pull_policy: build` also makes `docker compose pull` skip this service. The
image is only ever built locally, so without it a pull fails with
`pull access denied for weather-hub` — which is what stack managers that pull
before updating (e.g. Unraid's Compose Manager "Update Stack") would hit.

**If the build fails** with an error like `failed to evaluate path "https://...`,
your Docker Compose is using the newer "Bake" builder, which has a bug
handling remote git build contexts (seen on Docker Desktop for Windows).
Build the image directly instead, then bring the stack up without rebuilding:

```bash
docker build -t weather-hub:local https://github.com/dhiorns15/tempest-weather-hub.git#main
docker compose up -d --no-build
```

or clone the repo locally and change `build.context` in `docker-compose.yaml`
back to `.` instead.

The SQLite database lives under `./data`, mounted as a volume so history
survives container restarts/rebuilds.

## API

| Route | Auth | Notes |
|---|---|---|
| `GET /healthz` | none | liveness check |
| `GET /api/current` | none | latest cached observation |
| `GET /api/history?start=&end=&resolution=&format=&download=` | API key | raw/hourly/daily observations, as JSON or CSV |
| `GET /widget/current?theme=light\|dark\|auto` | none | embeddable HTML widget (see [Embedding a widget](#embedding-a-widget)) |
| `GET /kiosk/weather` | none | OpenWeatherMap-shaped output for Immich Kiosk's `custom_weather_url`, in whatever units `TEMPEST_UNITS` is set to (see note below) |
| `GET /api/forecast` | none | cached hourly/daily forecast |
| `GET /api/station-health` | none | battery/signal/uptime/firmware, only populated when `TEMPEST_UDP_ENABLED=true` |
| `GET /api/capabilities` | none | `{"cloud_configured": bool, "udp_configured": bool, "units": {...}, "timezone": "America/Denver"}` - which data source(s) are active, the display labels for the configured `TEMPEST_UNITS` (see below), and the station timezone daily history uses |
| `GET /api/alerts?limit=` | none | `{"enabled": bool, "alerts": [...]}` - most recent alerts first (default 50, max 200), see [Alerts](#alerts-optional) |
| `GET /site/records` | none | record highs/lows (all time, this year, this month) and "on this day" comparisons, for the History page - see [Records and comparisons](#records-and-comparisons) |
| `GET /site/wind-rose?start=&end=` | none | wind direction/speed counts for a range, for the wind roses - see [Wind rose](#wind-rose) |
| `/`, `/history.html`, ... | none | the site itself |

`units` in `/api/capabilities` is
`{"system": "imperial", "temp": "°F", "wind": "mph", "pressure": "inHg", "precip": "in", "distance": "mi"}`
or, with `TEMPEST_UNITS=metric`,
`{"system": "metric", "temp": "°C", "wind": "m/s", "pressure": "mb", "precip": "mm", "distance": "km"}`.
It's the one source of truth for unit labels: the site reads it on load
(falling back to imperial if the request fails), and the widget uses the
same table server-side. Unitless and fixed-unit values (humidity, UV, W/m²,
lux, kg/m³, minutes) don't depend on it.

**Kiosk unit mismatch:** Immich Kiosk's `custom_weather_url` fetcher does not
send a `units=` query param or convert values for custom providers — it only
does that for its own built-in OpenWeatherMap fetch path. It labels whatever
numbers `/kiosk/weather` returns using that weather location's own `unit:
metric|imperial` setting in Kiosk's config. Set that to match this hub's
`TEMPEST_UNITS` (imperial by default) or Kiosk will display correct Fahrenheit
values with a Celsius label (or vice versa).

`/api/current` and the widgets are intentionally unauthenticated — they're
meant to be embedded in public pages, and an API key baked into public HTML/JS
would leak immediately. `/api/history` is the gated surface for other
apps/developers to query your data programmatically. The site's own
`history.html` page reads from an unauthenticated `/site/history` route
(same query params/response shape) rather than the public keyed API, for the
same reason.

### History query params

- `start`, `end` — unix timestamps. Default to the last 24 hours if omitted.
- `resolution` — `raw` (every polled observation), `hourly`, or `daily`
  (aggregated via SQL `AVG`/`MIN`/`MAX`). `daily` buckets are the station's
  local calendar days (DST-aware, so the autumn change-over day is 25 hours);
  each `bucket` is that local date written as `YYYY-MM-DDT00:00:00Z`.
  `hourly` buckets are UTC hours (`bucket` is a real UTC instant), which are
  the same as local hours in any whole-hour-offset timezone. JSON responses
  include a top-level `"timezone"` saying which zone the daily buckets use
  (see `STATION_TIMEZONE`). `raw` is capped at a 31-day range — use
  `hourly`/`daily` for longer spans.
- `format` — `json` (default) or `csv`.
- `download` — `1` to add a `Content-Disposition: attachment` header so
  browsers save the response as a file instead of rendering it inline. The
  History page's "Download CSV"/"Download JSON" buttons use this against
  `/site/history` for whatever range is currently on screen.

### Records and comparisons

The History page's Records section reads `GET /site/records` (no
parameters, unauthenticated like `/site/history`). Everything is worked out
over the station's local calendar days - the same DST-aware days as daily
history (see `STATION_TIMEZONE`) - and times are shown in the station's
timezone.

- **Records**, for all time, this year and this month: highest and lowest
  temperature, peak gust, highest and lowest station pressure (each with
  the moment it was first reached - a later reading that only equals a
  record doesn't take it over), and the wettest day.
- **Wettest day** is the day with the largest daily rain total, which is
  the day's highest `precip_accum_local_day` (a running total that resets
  at local midnight). Readings from the first 15 minutes after midnight are
  ignored for it: the cloud's first reading of a day can still carry the
  previous day's total. Rain totals come from the cloud source only, so
  `rain_reported` says whether a scope has any rain data at all (a
  UDP-only station has none) and `wettest_day` is `null` when there's no
  data or no rain.
- **On this day** compares today so far with yesterday, the same date a
  week, a month and a year ago (a month back from the 31st is the last day
  of the shorter month): high, low, peak gust and rain. A day without
  readings comes back `"available": false` with a `reason` of
  `before_history` (before the first stored reading) or `no_data` (the
  station or this service recorded nothing that day), and `complete` flags
  days whose readings don't reach within an hour of both midnights. The
  comparisons fill in by themselves as history builds up.

```json
{"timezone": "America/Denver", "today": "2026-11-14", "first_day": "2026-07-27", "days_with_data": 90,
 "records": {
   "all_time": {"label": "All time", "start": null, "days": 90,
     "temp_high": {"value": 105.0, "date": "2026-08-02", "ts": 1785705547},
     "temp_low": {"value": 30.7, "date": "2026-10-17", "ts": 1792238905},
     "gust": {"value": 19.8, "date": "2026-10-10", "ts": 1791654727},
     "rain_reported": true, "wettest_day": {"value": 0.45, "date": "2026-09-20"},
     "pressure_high": {...}, "pressure_low": {...}},
   "this_year": {"label": "This year", "start": "2026-01-01", ...},
   "this_month": {"label": "This month", "start": "2026-11-01", ...}},
 "on_this_day": {
   "today": {"date": "2026-11-14", "available": true, "first_ts": 1794682376, "last_ts": 1794713018,
             "readings": 609, "complete": false, "temp_high": 51.0, "temp_low": 43.8, "gust": 8.0, "rain": 0.0},
   "comparisons": [
     {"key": "yesterday", "label": "Yesterday", "date": "2026-11-13", "available": false, "reason": "no_data"},
     {"key": "month", "label": "A month ago", "date": "2026-10-14", "available": true, "temp_high": 50.3, ...},
     ...]},
 "units": {"system": "imperial", "temp": "°F", ...}}
```

Finding records means a pass over the whole observations table, which grows
by about half a million rows a year with UDP on. The summaries of every day
before today are kept in memory for 10 minutes (and rebuilt when the
station's day or timezone changes); today's readings are read fresh on every
request, so a record set today shows straight away, and rows backfilled
into earlier days show within 10 minutes. On a copy of a real 152,000-row
history the full pass takes about 45 ms and a cached request about 1 ms; on
a 1.5-million-row copy (about three years) about 425 ms and 2 ms.

### Wind rose

`GET /site/wind-rose?start=&end=` (unix timestamps, the last 24 hours by
default, unauthenticated) counts the raw readings in the range by
direction and average wind speed (`wind_avg`). The History page draws it
for whatever range is selected; the current page shows the last 24 hours.

- **Directions:** 16 compass sectors of 22.5°, each centred on its bearing
  (N covers 348.75°-11.25°).
- **Calm:** an average speed below 1 mph (0.5 m/s in metric) - Beaufort
  force 0. Calm readings are counted on their own, not in a sector: the
  station reports a bearing of 0° for still air, so their direction means
  nothing.
- **Speed classes** (from the class's lower bound up to, not including, the
  next): 1-3, 3-5, 5-10, 10-15, 15-20 and 20+ mph, or 0.5-1.5, 1.5-2.5,
  2.5-5, 5-7.5, 7.5-10 and 10+ m/s. The low end is split finely because
  sustained wind at a home station is mostly light. The endpoint returns
  the classes and their labels, so the site hardcodes none of this.
- Each stored reading counts once. A reading without an average speed, or
  without a direction when it isn't calm, isn't counted at all, so
  `calm` plus every count always equals `total`. With both data sources on,
  the cloud's rows count too; while UDP is fresh they carry the UDP values,
  so they weight those minutes slightly more rather than adding other wind.

```json
{"start": 1794626678, "end": 1794713078, "unit": "mph", "calm_below": 1.0,
 "sectors": ["N", "NNE", "NE", ..., "NNW"], "sector_degrees": 22.5,
 "speed_classes": [{"min": 1.0, "max": 3.0, "label": "1–3 mph"}, ..., {"min": 20.0, "max": null, "label": "20+ mph"}],
 "counts": [[7, 0, 0, 0, 0, 0], [110, 94, 3, 0, 0, 0], ...],
 "calm": 42, "total": 609, "calm_share": 0.069}
```

`counts` has one row per sector, in `sectors` order, with one count per
speed class. A year of real history (152,000 readings) takes about 60 ms.

### Managing API keys

Keys are admin-issued via a CLI — there's no public signup flow. Only a
SHA-256 hash is ever stored; the plaintext key is shown once at creation.

```bash
python scripts/manage_keys.py create --label "my-app"
python scripts/manage_keys.py list
python scripts/manage_keys.py revoke <id>
```

Running via Docker Compose, use `docker compose exec` instead (the script shares
the running container's `data/weather.db`):

```bash
docker compose exec weather-hub python scripts/manage_keys.py create --label "my-app"
```

Use the printed key as a bearer token:

```bash
curl -H "Authorization: Bearer wh_..." "http://localhost:8080/api/history?resolution=daily"
```

### Backfilling missing history

While this service is down it records nothing, but the station keeps
reporting to Tempest's cloud, which keeps that history. With the cloud
source configured (`TEMPEST_STATION_ID` + `TEMPEST_TOKEN`), the gap can be
filled from there:

- **Automatically on startup.** If the newest stored row is more than 15
  minutes old, the missing span (at most `BACKFILL_MAX_DAYS`, 30 by default)
  is fetched in a background thread. Startup and polling don't wait for it,
  and a failure is only logged. Turn it off with `BACKFILL_ON_STARTUP=false`.
- **By hand**, for older holes, with `scripts/backfill_history.py`:

```bash
# See what's missing and what would be inserted, without writing anything
docker compose exec weather-hub python scripts/backfill_history.py --dry-run
# Fill every hole (gap longer than 15 minutes) between the oldest stored row and now
docker compose exec weather-hub python scripts/backfill_history.py
# Or only within a range; dates are UTC, and a date-only --end includes that whole day
docker compose exec weather-hub python scripts/backfill_history.py --start 2026-10-23 --end 2026-11-14
```

`--min-gap-minutes` (default 15) sets what counts as a hole, and `--pause`
(default 3 seconds) the wait between requests. Without Docker, run the same
script with `python scripts/backfill_history.py`.

Backfill only ever adds rows. It inserts timestamps that fall inside a gap
between stored rows, never within a minute of an existing row, and never
updates or replaces anything already stored. Running it twice inserts
nothing the second time. Each request is committed as it completes, so if
one fails part-way, re-running carries on from where it stopped.

Backfilled rows come from Tempest's device history
(`/observations/device/{device_id}`, the station's `ST` sensor), at a
**5-minute cadence**, the same as the REST poller's default interval. The
API picks the resolution from the length of the requested range: up to 1
day gives 1-minute data, up to 5 days gives 5-minute data, up to 30 days
gives 30-minute data, and anything longer gives 3-hour data. Backfill
therefore asks for windows of 1 to 5 days, so a three-week outage takes
five requests. The values are converted to `TEMPEST_UNITS` the same way the
UDP listener converts them.

Columns a backfilled row has: `air_temperature`, `relative_humidity`,
`wind_avg`, `wind_gust`, `wind_lull`, `wind_direction`, `station_pressure`,
`brightness` (illuminance, lux), `uv`, `solar_radiation` and
`precip_accum_local_day`. Everything else is NULL: `feels_like`,
`dew_point`, the wet bulb fields, `sea_level_pressure`, `air_density`,
`delta_t`, the lightning counts and distance, `precip_accum_local_yesterday`,
the rain-duration minutes, `precip_probability`, `conditions` and `icon`.
Those are derived or forecast values in `better_forecast`, and the history
endpoint doesn't return them. The lightning columns are rolling 1-hour and
3-hour totals, and the history only has per-interval strike counts.

`precip_accum_local_day` comes from the device's own local-day rain total
and resets at local midnight, the same as the live field. During the day it
can differ from what `better_forecast` reported live, because the live
figure gets revised as it goes. On one rainy day in October 2026, the live
total rose to 0.26 in, fell back to 0.20 in and ended at 0.24 in, while the
device history rose steadily to 0.24 in. Both ended the day at the same
total.

### Local UDP broadcast (optional, faster current conditions)

Tempest hubs broadcast raw observations on the local network over UDP
([protocol spec](https://weatherflow.github.io/Tempest/api/udp/v171/)) —
no internet or token required, roughly once a minute (plus a wind-only
update every ~3 seconds, not currently used here). Enabling this
(`TEMPEST_UDP_ENABLED=true`) updates current conditions and appends to
history from `obs_st` messages, using `LatestCache.update()` to merge in
just the raw sensor fields (temperature, humidity, wind including lull,
pressure, UV, solar radiation) without touching
`conditions`/`icon`/`location_name`/forecast — those are computed by
WeatherFlow's cloud and aren't in the local protocol at all, so the REST
poller (when configured) is their only source. Separately, `device_status`/
`hub_status` messages feed the Station Health panel (battery, signal
strength, uptime, firmware).

**Requirements:**
- This service must run on the **same local network** as your Tempest hub —
  it's LAN broadcast traffic, doesn't cross the internet or routers.
- Under Docker, the UDP port must be published (`docker-compose.yaml`
  already does this via `TEMPEST_UDP_PORT`). Verified working with Docker
  Desktop's default bridge networking — no `network_mode: host` needed.
- `TEMPEST_HUB_SERIAL` is optional but recommended if more than one Tempest
  hub could ever be on your network; find yours by watching the container
  logs briefly with `TEMPEST_HUB_SERIAL` unset, or from a raw UDP capture
  (the `hub_sn` field in any broadcast message).

### Running with only one data source

Neither source is required on its own - `TEMPEST_STATION_ID`/`TEMPEST_TOKEN`
and `TEMPEST_UDP_ENABLED` are each independently sufficient, and the app
refuses to start only if **neither** is configured. `GET /api/capabilities`
reports which are active (`"cloud_configured"`/`"udp_configured"`, alongside
the `units` labels described under [API](#api)), and the site/API adapt
automatically:

- **Cloud only** (UDP off): everything works as if UDP never existed -
  forecast, Kiosk output, all metrics, the Station Health panel just stays
  hidden (nothing to report without UDP's device/hub status messages).
- **UDP only** (no `TEMPEST_STATION_ID`/`TEMPEST_TOKEN`): current conditions
  and history/charts work for whatever UDP actually provides (temperature,
  humidity, wind, pressure, UV, solar radiation) - the numeric-only fields
  from `obs_st`. `conditions`/`icon`/`location_name`, forecast, and
  `/kiosk/weather` all require the cloud source specifically (they're
  computed by WeatherFlow's forecast model, not present in the local
  protocol at all) and are hidden/disabled cleanly rather than shown
  half-populated: the Hourly/Daily Forecast sections and the cloud-only
  tiles (rain, lightning, sun times) don't render, `/kiosk/weather` and
  `/api/forecast` return a clear 503 explaining what's missing, and
  REST-only charts (rain, lightning, the Advanced group) and REST-only
  lines within a shared chart (feels-like and dew point on the Temperature
  chart) don't render either.
- **Both**: full functionality, and both write to history independently.
  The cloud reports the sensors UDP also provides (temperature, humidity,
  wind, pressure, UV, solar radiation) rounded coarser, so while the last
  UDP reading is under 5 minutes old a cloud poll keeps the UDP values for
  those fields - in current conditions and in the history row it writes -
  and only adds its cloud-only fields. If UDP goes quiet, cloud values take
  over as normal. The raw-resolution charts likewise skip older cloud rows'
  readings of those fields wherever UDP readings sit within 5 minutes.

### Alerts (optional)

Off unless a destination is configured: set `ALERT_WEBHOOK_URL`, or
`ALERT_SMTP_HOST` + `ALERT_EMAIL_TO`, or both. Alerting runs in its own
threads, only ever reads the caches, and never writes observations, so a
slow or broken destination can't hold up data collection; a bad `ALERT_*`
value disables alerting (logged) rather than stopping the service.

| Kind | Fires when | Re-arms when |
|---|---|---|
| `no_data` | no new observation for `ALERT_NO_DATA_MINUTES` (default 15) - counted from the service start if nothing has arrived since | a new observation arrives, which also sends a "data resumed" notice |
| `frost` | temperature at or below `ALERT_FROST_TEMPERATURE` | it rises 2 °F / 1 °C above the threshold |
| `wind` | gust at or above `ALERT_WIND_GUST` | gusts drop 10 mph / 4.5 m/s below the threshold |
| `lightning` | a strike at or within `ALERT_LIGHTNING_DISTANCE` | 30 minutes pass with no strike that close |
| `rain` | rain starts | 60 minutes pass with no rain |
| `battery` | Tempest battery at or below `ALERT_BATTERY_VOLTAGE` (UDP `device_status`, so UDP only) | it recovers 0.05 V above the threshold |

Each kind fires once when its condition begins. After it re-arms, another
alert of the same kind is held back until `ALERT_COOLDOWN_MINUTES` (default
60) have passed since the last one - an episode that starts inside the
cooldown is tracked but not announced. `no_data` is exempt from the cooldown
(each outage already needs a recovery in between). Thresholds are in the
`TEMPEST_UNITS` system. Pick kinds with `ALERT_KINDS`, e.g.
`ALERT_KINDS=no_data,frost`.

With UDP enabled, lightning and rain alerts are instant: the listener hands
`evt_strike`, `evt_precip` and `obs_st` messages (after the
`TEMPEST_HUB_SERIAL` filter) straight to the alert evaluator. From the cloud
alone they lag by up to `POLL_INTERVAL_SECONDS`: lightning comes from the
"last strike" time and distance, rain from the day's rain total or rain
minutes going up between polls. With the cloud only, set
`ALERT_NO_DATA_MINUTES` comfortably above the poll interval (the cloud's
observation is itself up to a few minutes old when polled); the default 15
suits the default 300-second poll.

Alert state and a log of sent alerts are kept in the SQLite database (the
`alert_state` and `alert_log` tables), so a restart doesn't re-send alerts
for conditions still in progress. `GET /api/alerts` lists recent alerts
with a delivery `status` of `sent`, `partial`, `failed` or `pending`.

**Webhook formats** (`ALERT_WEBHOOK_FORMAT`):
- `json` (default) - a JSON POST that works as-is with Slack incoming
  webhooks (they read `text`) and Discord webhooks (they read `content`,
  capped at Discord's 2000 characters), with structured fields for
  anything else:
  ```json
  {"text": "Frost alert for Tower 1\nTemperature is 31.5°F, at or below the 32°F frost threshold.",
   "content": "(same as text)", "kind": "frost", "title": "Frost alert for Tower 1",
   "message": "Temperature is 31.5°F, at or below the 32°F frost threshold.",
   "station": "Tower 1", "time": 1794714059,
   "values": {"air_temperature": 31.5, "threshold": 32.0, "unit": "°F"}}
  ```
  `kind` is one of the kinds above, `data_resumed`, or `test`; `time` is a
  unix timestamp.
- `ntfy` - for an [ntfy](https://ntfy.sh) topic URL such as
  `https://ntfy.sh/your-topic`: the message as a plain-text body, plus
  `Title`, `Priority` (4 for no-data/frost/wind/lightning, 3 otherwise) and
  `Tags` headers. Set `ALERT_WEBHOOK_TOKEN` for a protected topic.

Failed deliveries are retried twice (after 10 s, then 60 s) and then given
up on; every request has a timeout. The logs show webhook URLs only as
`scheme://host/...` (they usually embed a secret) and never show the SMTP
password.

**Sending a test alert** once configured:

```bash
python scripts/send_test_alert.py
docker compose exec weather-hub python scripts/send_test_alert.py
```

It sends one alert to each destination (no retries), prints the outcome,
records it in the alert log, and exits non-zero if anything failed.

The no-data alert runs inside this service, so it can't report the service
itself being down or the host being off - pair it with an external uptime
check on `/healthz` if that matters to you.

### Embedding a widget

```html
<iframe
  src="http://your-host:8080/widget/current?theme=auto"
  width="300" height="90" style="border:none;" title="Current weather">
</iframe>
```

A compact card in the site's style: the same weather icon as the site, the
temperature with its unit (`TEMPEST_UNITS`), the conditions text, and a line
of secondary readings (feels-like, wind, humidity).

- **Size:** designed for 200-400 px wide and about 90 px tall. Narrower
  frames drop secondary readings from the end rather than squeezing them; in
  a frame under about 80 px tall the secondary line is left out entirely.
- **`theme`:** `light` (default), `dark`, or `auto` to follow the viewer's
  light/dark preference (`prefers-color-scheme`). The corners outside the
  rounded card are transparent, so it sits on any page background.
- **Self-contained:** one HTML document with inline CSS and JS and no
  requests of its own. The icon artwork is `static/js/icons.js` inlined at
  render time, so it always matches the site; if the frame blocks scripts
  (`sandbox` without `allow-scripts`) everything but the icon still shows.
- **Missing data:** before the first reading it shows `--` and "Waiting for
  data…". UDP-only setups have no icon or conditions text, so the widget
  shows the temperature and secondary line without them.
- It renders the latest reading when loaded and doesn't refresh itself;
  reload the frame if you need it live.

## Tracked metrics

History (and its charts) cover every numeric field Tempest's `better_forecast`
exposes: temperature, feels-like, dew point, wet bulb/wet bulb globe
temperature, humidity, wind (lull/avg/gust/direction), station and sea-level
pressure, UV, solar radiation, brightness, precipitation (today, yesterday,
probability, rain-duration today/yesterday), lightning (strikes in the last
1hr/3hr, nearest-strike distance), air density, and delta T. The current page
and history page both chart all of it: the everyday charts are always shown,
with Lightning and Advanced groups collapsed by default (the browser remembers
which you've opened). Closely related fields share a chart — temperature,
feels-like and dew point; wind lull/avg/gust; today's and yesterday's rain —
capped at three lines each so they stay tellable apart. See
`static/js/charts.js`'s `CHARTS`/`CHART_LAYOUT` — add a field there and it
appears on both pages automatically. Sunrise/sunset and pressure trend are
shown live but not charted (they're not the kind of thing you track a trend
line for). Station health (battery, signal, uptime, firmware) is a separate
panel sourced from the UDP listener's `device_status`/`hub_status` messages,
not weather data — see `/api/station-health` above.

## Theme

Both pages have an Auto / Light / Dark switch in the top bar. The choice is
remembered per browser (in `localStorage`; if storage is blocked the switch
still works, it just isn't remembered).

- **Auto** (the default) follows the OS light/dark setting, and goes dark for
  night and thunderstorm skies whatever the OS says.
- **Light** stays light even at night: night and storm skies have light
  versions.
- **Dark** stays dark in daytime, using the dark versions of the daytime skies.

`static/css/site.css` holds the dark tokens and dark skies once, in a single
`@media (prefers-color-scheme: dark)` block. `static/js/theme.js` is loaded as a
blocking script in `<head>`, so the choice is applied before the first paint
with no flash of the wrong theme. It re-points that block's media query (`all`
to force dark, `not all` to force light, the OS query again for Auto), sets
`data-theme` (the theme actually showing) and `data-theme-pref` (the choice) on
`<html>`, keeps `color-scheme` and the `theme-color` metas in step, and
repaints the charts. Any new rule written inside a
`prefers-color-scheme: dark` query in `site.css` follows the switch
automatically. With JavaScript off the switch is hidden and the site simply
follows the OS setting.

## Installing as an app

The site has a web app manifest (`static/manifest.webmanifest`), icons
(`static/icons/`, including a maskable version and an `apple-touch-icon`) and a
service worker (`static/sw.js`) with an offline page (`static/offline.html`).

How much of that a browser uses depends on how you reach the site. Browsers
only allow service workers and their "Install app" prompt in a **secure
context**: HTTPS, or `http://localhost` on the machine itself. A plain-HTTP LAN
address such as `http://192.168.1.10:8080` is not one.

| | Plain HTTP on a LAN address | HTTPS (or `http://localhost`) |
|---|---|---|
| Theme switch, browser toolbar colour | Yes | Yes |
| Browser's "Install app" / standalone app window | No | Yes |
| Service worker and offline page | No (not registered; nothing else is affected) | Yes |
| Home-screen shortcut via the browser menu ("Add to Home Screen", "Create shortcut") | Often possible, but as an ordinary shortcut; whether it opens as an app window or a normal tab depends on the browser (not tested) | Yes, as an installed app |

To get the installable app on other devices, serve the hub over HTTPS with a
certificate those devices trust, for example behind a reverse proxy (Caddy,
Nginx Proxy Manager, Traefik, Tailscale Serve). The hub needs no extra
configuration for that.

The service worker is network-first for everything it handles, so while
you're online you always get the current files from the server and an update
shows up on the next load; its cache is only used when the network fails.
Live data (`/api/`, `/site/`, `/widget/`, `/kiosk/`, `/healthz`) is never
cached: offline, a page that can't load shows the offline page ("Can't reach
Weather Hub") instead of old readings.

The PNG icons were generated once from `static/favicon.svg` and are committed;
there is no build step. The maskable and apple-touch versions put the artwork
on a full-bleed square, scaled down (to 90% and 85%) so it stays inside the
maskable safe zone. To regenerate them, rasterise with any SVG tool, e.g.
`rsvg-convert -w 512 -h 512 static/favicon.svg -o static/icons/icon-512.png`.

## Roadmap / backlog

Shipped: current conditions + hourly/daily forecast (grouped by day) +
history (with per-metric charts and custom date ranges) + admin-issued API
keys + an embeddable current-conditions widget + weather-condition page
backgrounds + Kiosk-compatible output (`/kiosk/weather`) + an optional local
UDP listener for faster current-conditions updates and its own history +
station health monitoring + the full set of Tempest-provided metrics
tracked and charted + running with only one data source configured (cloud
or UDP, not both required).

Not yet built (ideas from the original plan, pick up if interesting):
- Backfill history further back than "since this app started" via Tempest's
  historical stats endpoint, if it exposes enough range.
- CSV/JSON export of a chosen date range.
- Record highs/lows, "on this day" comparisons.
- Alerts (frost, high wind) via webhook or email — the UDP listener's
  `evt_strike`/`evt_precip` messages would make instant versions of these
  possible, not just REST-poll-interval-delayed ones.
- More widget styles: compact badge, detailed card, mini sparkline chart.
- Per-key rate limiting + usage counters.
- Multi-station support (built once already for `tempest-kiosk-weather-bridge`
  and reverted there — could be re-applied here).
- Manual dark/light theme toggle (the site follows the OS setting, and
  always goes dark for night/storm conditions) + PWA installability.
- RSS/JSON daily-summary feed.
- History at UDP's ~1-minute cadence instead of the REST poll's 5 minutes,
  if finer-grained charts are wanted (kept out of this pass to avoid the
  added complexity of computing a running daily rain total from UDP's
  per-minute deltas).

## Known limitations

- `daily` history buckets follow the station's local calendar day, but
  `hourly` buckets are UTC hours, so in a timezone with a half-hour offset
  (e.g. India, Newfoundland) each hourly bucket straddles two local hours.
  UDP-only setups that have never been cloud-connected bucket days in UTC
  unless `STATION_TIMEZONE` is set.
- The wettest-day record is the day's highest running rain total. The
  cloud's live total is sometimes revised down during the day (see
  [Backfilling missing history](#backfilling-missing-history)), so on such a
  day the record can sit a few hundredths above the total the day ended on.
- `air_density` and `delta_t` are specialized (aviation/agricultural) metrics
  included for completeness since Tempest reports them, but have limited
  everyday display value.
- The UDP listener only ever freshens temperature/humidity/wind/pressure/UV/
  solar radiation — it deliberately does not touch precipitation or lightning
  fields, since UDP's versions of those have different semantics (e.g.
  "rain in the last minute" vs. a running daily total) than the REST-sourced
  fields of the same general subject.
- Only a single `/widget/current` style exists so far (see backlog).
- The widget has been checked in `<iframe>`s in headless Chromium (Playwright
  1.49.1) at 200-400 px wide, in all three themes, with both OS colour
  schemes. It hasn't been checked in Firefox or Safari.
