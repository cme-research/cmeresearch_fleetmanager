# cmeresearch_fleetmanager — Implementation Plan

Status: **draft, awaiting Phase 0 kickoff**
Last updated: 2026-05-24

---

## 1. Goals & Scope

Django web app that:
- Lists and manages AGVs (CRUD by IP and/or UUID).
- Continuously probes each AGV over the VPN and stores availability samples (~1/min).
- Accepts push heartbeats from AGVs (so an AGV can also actively report).
- Shows per-AGV and fleet-wide uptime dashboards with day / week / month aggregation.
- Shows "last seen at" and current online status at a glance.

**Out of scope for v1** (later): remote control, log streaming, OTA, fleet-wide commands,
alerting (email/Slack/MQTT alerts on outage), per-user permissions / multi-tenant.

---

## 2. Locked-in Decisions

| Topic | Choice |
|---|---|
| Probe method | ICMP ping every 60 s **and** `POST /api/heartbeat/` push endpoint |
| Deployment | Docker Compose on a LAN server |
| Auth | Login required for all views (Django auth; admin creates users) |
| DB | PostgreSQL 16 in its own container with persistent volume |

Open sub-decision (decide before Phase 6):
- VPN routing for the watchdog: **(a)** run compose on a host that's already a VPN client,
  with the watchdog in `network_mode: host` *(recommended, simplest)*, or **(b)** a dedicated
  WireGuard sidecar container that the watchdog routes through.

---

## 3. High-Level Architecture

```
                ┌────────────────────────────────────────────────┐
                │              cmeresearch_fleetmanager          │
                │                                                │
   Browser ───► │  web (gunicorn + Django)  ──┐                  │
                │                             ├── db (Postgres)  │
   AGV  ──HTTP─►│  /api/heartbeat/   ─────────┘                  │
   (push)       │                             ▲                  │
                │  watchdog (asyncio loop) ───┘                  │
                │  pings AGVs every 60s over VPN                 │
                └────────────────────────────────────────────────┘
                            │ ICMP / TCP probe
                            ▼
                  AGVs on VPN (192.168.x.x / 10.x.x.x)
```

Two independent processes sharing one DB:
1. **web** — gunicorn + Django (UI + API).
2. **watchdog** — long-running Python process that loads AGVs from DB, probes each on a
   schedule, writes `UptimeSample` rows, and runs the nightly aggregation.

Web restarts don't disturb monitoring; we can scale either independently.

---

## 4. Tech Stack

| Concern | Choice | Why |
|---|---|---|
| Framework | Django 5.x | Stable, batteries-included |
| Python | 3.12 | Current, async-friendly |
| DB (prod) | PostgreSQL 16 | Handles 26M+ rows/year easily, good aggregation |
| DB (dev) | SQLite (fallback) or the same Postgres container | Zero-config local dev |
| API | Django REST Framework | Heartbeat endpoint, future-proof |
| Scheduler | Custom `asyncio` management command + `icmplib.async_multiping` | ~50 AGVs in one async loop; no Celery/Redis needed |
| Frontend | Django templates + Bootstrap 5 + HTMX | Lightweight, no SPA build step |
| Charts | Chart.js (CDN) | Simple time-series, no build step |
| Auth | Django built-in | Internal tool |
| Deploy | Docker Compose | Already decided |

---

## 5. Data Model

```python
# core/models.py

class AGV(models.Model):
    uuid          = UUIDField(unique=True, default=uuid.uuid4)
    name          = CharField(max_length=100, unique=True)
    description   = TextField(blank=True)
    vpn_ip        = GenericIPAddressField(null=True, blank=True, unique=True)
    hostname      = CharField(max_length=255, blank=True)
    enabled       = BooleanField(default=True)              # pause without deleting
    probe_method  = CharField(choices=["icmp","tcp","http"], default="icmp")
    probe_port    = IntegerField(null=True, blank=True)     # for tcp/http
    created_at    = DateTimeField(auto_now_add=True)
    updated_at    = DateTimeField(auto_now=True)

    # Denormalised for fast dashboard rendering:
    last_seen_at      = DateTimeField(null=True, blank=True)
    last_state        = CharField(choices=["online","offline","unknown"], default="unknown")
    last_response_ms  = FloatField(null=True, blank=True)


class UptimeSample(models.Model):
    agv         = ForeignKey(AGV, on_delete=CASCADE, related_name="samples")
    timestamp   = DateTimeField(db_index=True)
    is_online   = BooleanField()
    response_ms = FloatField(null=True, blank=True)
    source      = CharField(choices=["probe","heartbeat"], default="probe")
    note        = CharField(max_length=200, blank=True)   # error reason, etc.

    class Meta:
        indexes  = [models.Index(fields=["agv", "timestamp"])]
        ordering = ["-timestamp"]


class UptimeDailyAggregate(models.Model):
    """Pre-aggregated for fast week/month dashboards."""
    agv                    = ForeignKey(AGV, on_delete=CASCADE, related_name="daily_aggregates")
    date                   = DateField(db_index=True)
    samples_total          = IntegerField()
    samples_online         = IntegerField()
    uptime_seconds         = IntegerField()
    longest_outage_seconds = IntegerField()

    class Meta:
        unique_together = ("agv", "date")
```

**Storage sizing sanity check:** 1 sample / min × 60 × 24 × 365 ≈ 525 k rows/year/AGV.
50 AGVs ≈ 26 M rows/year. Postgres handles this with the composite index.
Retention policy (later): raw samples 90 days, aggregates forever.

---

## 6. Watchdog Design

Django management command: `python manage.py run_watchdog`.

```
Every PROBE_INTERVAL_S (default 60):
  1. Reload enabled AGVs from DB
  2. asyncio.gather(probe(agv) for agv in agvs)
       - ICMP via icmplib.async_multiping (parallel)
       - or TCP connect to probe_port
       - or HTTP GET to a health URL
  3. For each result: create UptimeSample + update AGV.last_*
  4. Sleep until next tick
```

- `icmplib.async_multiping` pings the whole fleet in one shot, non-blocking.
- Container needs `cap_add: [NET_RAW]` (or `net.ipv4.ping_group_range` sysctl) so ICMP
  works without root.
- Graceful DB-loss handling on startup (retry loop).
- Heartbeat endpoint `POST /api/heartbeat/` accepts `{"uuid": "...", "timestamp": "..."}`
  → creates a sample with `source="heartbeat"` and updates `last_seen_at`. This covers
  the *push* side of "is IP X connecting to the server"; the active probe covers the *pull* side.
- Nightly aggregation job at 00:05 local time fills `UptimeDailyAggregate` for the previous day.

---

## 7. Views & URLs

| URL | View | Purpose |
|---|---|---|
| `/` | `FleetOverview` | Grid/table: name, IP, status dot, last seen, today's uptime %, 24 h sparkline |
| `/agvs/add/` | `AGVCreate` | Form: name, vpn_ip, uuid (optional, auto), probe method |
| `/agvs/<uuid>/` | `AGVDetail` | Big chart (1d/7d/30d/custom), stats, recent samples |
| `/agvs/<uuid>/edit/` | `AGVUpdate` | Edit form |
| `/agvs/<uuid>/delete/` | `AGVDelete` | Confirm delete |
| `/dashboard/` | `MultiAGVDashboard` | Multi-AGV comparison; uptime heatmap |
| `/api/heartbeat/` | `HeartbeatView` (DRF) | AGV push endpoint |
| `/api/agv/<uuid>/samples/` | `SampleListView` | JSON time-series for Chart.js |
| `/admin/` | Django admin | Power-user fallback |

**Dashboard visualisations:**
- Status dot (green/red/grey) + last-seen relative time.
- Per-AGV detail: line chart online (1)/offline (0) over window; bar chart of daily uptime %.
- Heatmap (day × hour) of online % over the last 30 days.
- Time-range selector: Today / Last 7 days / Last 30 days / Custom.
- Day-level data from `UptimeSample` directly; 7d/30d from `UptimeDailyAggregate`.

---

## 8. Project Layout

```
cmeresearch_fleetmanager/
├── manage.py
├── pyproject.toml                # or requirements.txt
├── README.md
├── PLAN.md                       # this file
├── .gitignore
├── fleetmanager/                 # Django project package
│   ├── settings/
│   │   ├── base.py
│   │   ├── dev.py
│   │   └── prod.py
│   ├── urls.py
│   └── wsgi.py
├── core/                         # AGV model + views
│   ├── models.py
│   ├── views.py
│   ├── forms.py
│   ├── urls.py
│   ├── templates/core/
│   ├── admin.py
│   └── management/commands/
│       ├── run_watchdog.py
│       └── aggregate_uptime.py
├── api/                          # DRF endpoints
│   ├── views.py
│   ├── serializers.py
│   └── urls.py
├── dashboards/                   # multi-AGV dashboards
│   ├── views.py
│   ├── urls.py
│   └── templates/dashboards/
├── static/
├── templates/                    # base.html, navbar, login
└── deploy/
    ├── Dockerfile
    ├── docker-compose.yml
    ├── docker-compose.override.yml.example
    ├── nginx/nginx.conf.example
    └── README.md                 # VPN + capability notes
```

---

## 9. Docker Compose Shape

`docker-compose.yml` services:

| Service | Image / Build | Role |
|---|---|---|
| `db` | `postgres:16-alpine` | DB, persistent volume `pgdata`; not exposed to LAN |
| `web` | local Dockerfile (Python 3.12, gunicorn) | Django UI + DRF API |
| `watchdog` | same image, different entrypoint (`python manage.py run_watchdog`) | ICMP probe loop + nightly aggregation |
| `nginx` *(optional)* | `nginx:alpine` | TLS termination + static files |

Notes:
- `watchdog` needs `cap_add: [NET_RAW]` (or `sysctls: net.ipv4.ping_group_range=0 65535`).
- VPN routing: prefer host-mode for `watchdog` on a VPN-connected host (simplest).
  Alternative: dedicated WireGuard sidecar.
- `db` stays on the internal compose network only.
- `.env` holds `POSTGRES_PASSWORD`, `DJANGO_SECRET_KEY`, `ALLOWED_HOSTS`,
  `PROBE_INTERVAL_S`, `PROBE_TIMEOUT_MS`.

---

## 10. Config & Secrets

- `.env` loaded via `django-environ` — never committed.
- Settings split (`dev` / `prod`); `DJANGO_SETTINGS_MODULE` picks one.
- Key env vars:
  - `DJANGO_SECRET_KEY`
  - `DATABASE_URL`
  - `ALLOWED_HOSTS`
  - `PROBE_INTERVAL_S` (default 60)
  - `PROBE_TIMEOUT_MS` (default 2000)

---

## 11. Build Phases

| Phase | Deliverable | Demoable |
|---|---|---|
| **0** | Django scaffold, settings split, base template, login screen, `Dockerfile`, `docker-compose.yml` with `db` + `web` | `docker compose up` → log in |
| **1** | `AGV` model + admin + list/add/edit/delete UI | Add an AGV via UI |
| **2** | `UptimeSample` + `run_watchdog` (ICMP) + `watchdog` compose service + status dot on list | See AGV go green/red |
| **3** | Per-AGV detail page with Chart.js timeline + time-range selector | Show last 24 h |
| **4** | `UptimeDailyAggregate` + nightly aggregation + week/month views + heatmap | "Show me May uptime" |
| **5** | `/api/heartbeat/` (DRF) + example AGV-side push script | AGV pushes heartbeat |
| **6** | nginx + TLS + deploy README + VPN routing notes for watchdog | Running on real LAN server |

Each phase = one small reviewable PR.

---

## 12. Next Steps

1. Decide VPN routing approach (host-mode vs WireGuard sidecar) — can defer to Phase 6.
2. Kick off **Phase 0**: scaffold Django project + base Docker Compose.
