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
| `TEMPEST_UNITS` | No | `imperial` | `metric` or `imperial` |
| `PORT` | No | `8080` | HTTP port to listen on |
| `POLL_INTERVAL_SECONDS` | No | `300` | Seconds between Tempest polls |
| `DB_PATH` | No | `data/weather.db` | Path to the SQLite history file |
| `STATIC_DIR` | No | `static` | Path to the static site directory |
| `TEMPEST_UDP_ENABLED` | One of this, or the cloud pair | `false` | `true` to listen for the station's local UDP broadcast (see below) |
| `TEMPEST_UDP_PORT` | No | `50222` | UDP port to listen on - Tempest hubs always broadcast to 50222, so there's rarely a reason to change this |
| `TEMPEST_HUB_SERIAL` | No | — | Optional `hub_sn` filter (e.g. `HB-00118618`); blank accepts any hub heard |

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

```bash
cp .env.example .env   # fill in your values
docker compose up -d --build
```

The SQLite database lives under `./data`, mounted as a volume so history
survives container restarts/rebuilds.

## API

| Route | Auth | Notes |
|---|---|---|
| `GET /healthz` | none | liveness check |
| `GET /api/current` | none | latest cached observation |
| `GET /api/history?start=&end=&resolution=` | API key | raw/hourly/daily observations |
| `GET /widget/current?theme=light\|dark` | none | embeddable HTML widget |
| `GET /kiosk/weather` | none | OpenWeatherMap-shaped output for Immich Kiosk's `custom_weather_url` |
| `GET /api/forecast` | none | cached hourly/daily forecast |
| `GET /api/station-health` | none | battery/signal/uptime/firmware, only populated when `TEMPEST_UDP_ENABLED=true` |
| `GET /api/capabilities` | none | `{"cloud_configured": bool, "udp_configured": bool}` - which data source(s) are active |
| `/`, `/history.html`, ... | none | the site itself |

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
  (aggregated via SQL `AVG`/`MIN`/`MAX`, bucketed in UTC). `raw` is capped at
  a 31-day range — use `hourly`/`daily` for longer spans.

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
reports which are active (`{"cloud_configured": bool, "udp_configured": bool}`),
and the site/API adapt automatically:

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
  half-populated: the Hourly/Daily Forecast sections don't render, `/kiosk/
  weather` and `/api/forecast` return a clear 503 explaining what's missing,
  and REST-only chart groups (Precipitation, Lightning, Advanced, plus
  individual REST-only cards within Temperature & Comfort / Pressure & Light)
  don't render either.
- **Both**: full functionality, current conditions refresh from whichever
  source last reported (UDP roughly every minute, REST every
  `POLL_INTERVAL_SECONDS`), and both write to history independently.

### Embedding a widget

```html
<iframe
  src="http://your-host:8080/widget/current?theme=light"
  width="260" height="70" style="border:none;">
</iframe>
```

## Tracked metrics

History (and its charts) cover every numeric field Tempest's `better_forecast`
exposes: temperature, feels-like, dew point, wet bulb/wet bulb globe
temperature, humidity, wind (lull/avg/gust/direction), station and sea-level
pressure, UV, solar radiation, brightness, precipitation (today, yesterday,
probability, rain-duration today/yesterday), lightning (strikes in the last
1hr/3hr, nearest-strike distance), air density, and delta T. The current page
and history page both chart all of it, grouped into Temperature & Comfort,
Wind, Pressure & Light, Precipitation, Lightning, and Advanced sections (see
`static/js/charts.js`'s `METRICS`/`CHART_LAYOUT` — add a field there and it
appears on both pages automatically). Sunrise/sunset and pressure trend are
shown live but not charted (they're not the kind of thing you track a trend
line for). Station health (battery, signal, uptime, firmware) is a separate
panel sourced from the UDP listener's `device_status`/`hub_status` messages,
not weather data — see `/api/station-health` above.

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
- Dark/light theme toggle + PWA installability for the site itself.
- RSS/JSON daily-summary feed.
- History at UDP's ~1-minute cadence instead of the REST poll's 5 minutes,
  if finer-grained charts are wanted (kept out of this pass to avoid the
  added complexity of computing a running daily rain total from UDP's
  per-minute deltas).

## Known limitations

- History aggregation (`hourly`/`daily`) buckets in UTC, not the station's
  local calendar day.
- `air_density` and `delta_t` are specialized (aviation/agricultural) metrics
  included for completeness since Tempest reports them, but have limited
  everyday display value.
- The UDP listener only ever freshens temperature/humidity/wind/pressure/UV/
  solar radiation — it deliberately does not touch precipitation or lightning
  fields, since UDP's versions of those have different semantics (e.g.
  "rain in the last minute" vs. a running daily total) than the REST-sourced
  fields of the same general subject.
- Only a single `/widget/current` style exists so far (see backlog).
- Widget rendering has only been verified via direct HTTP requests (curl),
  not visually in a real browser `<iframe>` — this environment has no
  browser/screenshot tooling available. Worth a manual look before relying
  on it for a real embed.
