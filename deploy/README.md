# Deploy notes

Everything in this folder is for running the fleetmanager on a LAN server via
Docker Compose. The web app + watchdog + Postgres run as three containers;
nginx is opt-in for TLS.

## Layout

```
deploy/
├── Dockerfile                          # image used by both web and watchdog
├── docker-compose.yml                  # db, web, watchdog (+ nginx via profile)
├── docker-compose.override.yml.example # copy for hot-reload dev
└── nginx/
    ├── nginx.conf.example              # reverse proxy config
    └── certs/                          # mount your TLS cert + key here
```

---

## Quick start (prod-style, no TLS)

```bash
cp .env.example .env
# Set at least: DJANGO_SECRET_KEY, POSTGRES_PASSWORD, DJANGO_ALLOWED_HOSTS

docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml exec web \
  python manage.py createsuperuser
```

Browse to <http://server:8000/> and log in.

## Dev mode (hot reload)

```bash
cp deploy/docker-compose.override.yml.example deploy/docker-compose.override.yml
docker compose -f deploy/docker-compose.yml \
               -f deploy/docker-compose.override.yml \
               up --build
```

This mounts the source tree into the `web` container and runs `runserver`
instead of gunicorn.

## With TLS (production)

1. Drop your certificate and private key in `deploy/nginx/certs/` as
   `fullchain.pem` and `privkey.pem`. For a public domain use Let's Encrypt
   (certbot); for an internal LAN use your internal CA or a self-signed cert.
2. Edit `deploy/nginx/nginx.conf.example` if you need a real `server_name`
   (the default catches all hosts).
3. Start everything including nginx with the `tls` profile:

   ```bash
   docker compose -f deploy/docker-compose.yml --profile tls up -d --build
   ```

### Self-signed cert (dev / lab)

```bash
mkdir -p deploy/nginx/certs
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout deploy/nginx/certs/privkey.pem \
  -out    deploy/nginx/certs/fullchain.pem \
  -subj "/CN=fleetmanager.lan"
```

Browsers will warn; that's expected for self-signed.

---

## VPN routing for the watchdog

The watchdog needs network access **from its container to each AGV's
VPN IP**. The shipped `docker-compose.yml` uses **host-mode networking**
for the watchdog by default; the alternative (sidecar VPN container) is
documented below for the case where the host can't run a VPN client.

### Default: host is on the VPN, watchdog uses `network_mode: host`

This is what `deploy/docker-compose.yml` configures out of the box. The
Docker host itself is the VPN client (WireGuard / OpenVPN running on the
host OS); the watchdog shares the host's network namespace and reaches AGVs
over the host's tunnel.

To keep the watchdog able to reach Postgres from host-mode, the `db` service
publishes 5432 on the host's loopback (`127.0.0.1:5432:5432`) and the
watchdog connects to `127.0.0.1:5432`. The web container still reaches
Postgres via the compose bridge (`db:5432`), and the loopback binding keeps
the DB off the LAN.

Pros: simplest, no extra container, works with any VPN tech the host supports.
Cons: watchdog shares the host's network namespace (less isolation).

### Alternative: dedicated WireGuard sidecar container

Use this when the host is NOT a VPN client (e.g. a cloud VM where you don't
want to put VPN config on the host). Remove `network_mode: host` from the
watchdog and add an override along these lines:

```yaml
services:
  wg:
    image: linuxserver/wireguard
    cap_add: [NET_ADMIN, SYS_MODULE]
    sysctls:
      net.ipv4.ip_forward: "1"
    volumes:
      - ./wg-config:/config
    restart: unless-stopped

  watchdog:
    network_mode: "service:wg"
    depends_on:
      wg:
        condition: service_started
```

The watchdog shares the wg container's network namespace, so its traffic
egresses through the WireGuard tunnel.

### ICMP and capabilities

Either way, the watchdog container needs to open ICMP sockets for `icmplib`.
The compose file already sets `net.ipv4.ping_group_range=0 65535` and
`cap_add: NET_RAW`. On older kernels (<3.0) only the capability matters.
If you see "Permission denied" trying to ping, double-check both are in effect:

```bash
docker compose exec watchdog cat /proc/sys/net/ipv4/ping_group_range
docker compose exec watchdog capsh --print | grep cap_net_raw
```

---

## Operations

```bash
# Logs
docker compose logs -f web
docker compose logs -f watchdog

# Manual aggregation (idempotent — useful after backfilling samples)
docker compose exec web python manage.py aggregate_uptime --days 30

# Single probe cycle for debugging
docker compose exec watchdog python manage.py run_watchdog --once

# Postgres shell
docker compose exec db psql -U fleetmanager fleetmanager

# Backup
docker compose exec db pg_dump -U fleetmanager fleetmanager \
  | gzip > backup-$(date +%F).sql.gz

# Restore
gunzip -c backup-YYYY-MM-DD.sql.gz \
  | docker compose exec -T db psql -U fleetmanager fleetmanager
```

## Updating

```bash
git pull
docker compose -f deploy/docker-compose.yml build --pull
docker compose -f deploy/docker-compose.yml up -d
# migrations run automatically on web container startup
```

## Pruning old samples (optional)

The schema retains raw `UptimeSample` rows forever. For a long-running
deployment, prune raw samples older than N days (aggregates stay):

```bash
docker compose exec web python manage.py shell -c "
from datetime import timedelta
from django.utils import timezone
from core.models import UptimeSample
cutoff = timezone.now() - timedelta(days=90)
n, _ = UptimeSample.objects.filter(timestamp__lt=cutoff).delete()
print(f'pruned {n} samples')
"
```
