# cmeresearch_fleetmanager

Django web app to manage and monitor a fleet of AGVs over a VPN. Tracks per-AGV
uptime via ICMP probing and an optional push-heartbeat endpoint, and renders
per-day / per-week / per-month dashboards.

See [`PLAN.md`](PLAN.md) for the full design and roadmap.

---

## Quickstart (Docker Compose, dev)

```bash
cp .env.example .env
# edit .env: at minimum set DJANGO_SECRET_KEY and POSTGRES_PASSWORD

cp deploy/docker-compose.override.yml.example deploy/docker-compose.override.yml

docker compose -f deploy/docker-compose.yml \
               -f deploy/docker-compose.override.yml \
               up --build
```

Then in a second shell, create a superuser:

```bash
docker compose -f deploy/docker-compose.yml exec web \
  python manage.py createsuperuser
```

Browse to <http://localhost:8000/> and log in.

---

## Quickstart (no Docker, just Django)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # set DJANGO_SECRET_KEY at minimum
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The default `dev` settings fall back to SQLite when `DATABASE_URL` is not set.

---

## Layout

```
fleetmanager/        Django project package (settings split: base / dev / prod)
core/                AGV app: models, views, management commands
api/                 DRF heartbeat endpoint
templates/           base.html, login page, core templates
static/              CSS/JS
examples/            AGV-side heartbeat client
deploy/              Dockerfile + docker-compose for web + watchdog + db (+ nginx)
PLAN.md              Full design and phased roadmap
```

---

## Tests

```bash
python manage.py test
```
