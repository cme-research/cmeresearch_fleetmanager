"""Tests for the core app: models, forms, CRUD views, dashboards, JSON endpoints."""
import json
import socket
import threading
import uuid as uuid_lib
from datetime import date as date_cls, timedelta
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AGV, UptimeDailyAggregate, UptimeSample

User = get_user_model()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class AGVModelTests(TestCase):
    def test_str_is_name(self):
        agv = AGV.objects.create(name="bot-A", vpn_ip="10.0.0.1")
        self.assertEqual(str(agv), "bot-A")

    def test_get_absolute_url_resolves(self):
        agv = AGV.objects.create(name="bot-B", vpn_ip="10.0.0.2")
        url = agv.get_absolute_url()
        self.assertIn(str(agv.uuid), url)

    def test_name_unique(self):
        AGV.objects.create(name="dup", vpn_ip="10.0.0.3")
        with self.assertRaises(IntegrityError):
            AGV.objects.create(name="dup", vpn_ip="10.0.0.4")

    def test_vpn_ip_unique_when_set(self):
        AGV.objects.create(name="ipA", vpn_ip="10.0.0.5")
        with self.assertRaises(IntegrityError):
            AGV.objects.create(name="ipB", vpn_ip="10.0.0.5")

    def test_uuid_is_assigned(self):
        agv = AGV.objects.create(name="uuidA", vpn_ip="10.0.0.6")
        # Should be a valid UUID
        uuid_lib.UUID(str(agv.uuid))

    def test_default_state_unknown(self):
        agv = AGV.objects.create(name="stateA", vpn_ip="10.0.0.7")
        self.assertEqual(agv.last_state, AGV.State.UNKNOWN)


class UptimeDailyAggregateTests(TestCase):
    def setUp(self):
        self.agv = AGV.objects.create(name="agg-bot", vpn_ip="10.0.0.10")

    def test_uptime_pct_normal(self):
        a = UptimeDailyAggregate.objects.create(
            agv=self.agv, date=date_cls(2026, 5, 1),
            samples_total=100, samples_online=87,
            uptime_seconds=87 * 60, longest_outage_seconds=300,
        )
        self.assertAlmostEqual(a.uptime_pct, 87.0)

    def test_uptime_pct_zero_total(self):
        a = UptimeDailyAggregate.objects.create(
            agv=self.agv, date=date_cls(2026, 5, 2),
            samples_total=0, samples_online=0,
            uptime_seconds=0, longest_outage_seconds=0,
        )
        self.assertEqual(a.uptime_pct, 0.0)


# ---------------------------------------------------------------------------
# Form validation
# ---------------------------------------------------------------------------

class AGVFormTests(TestCase):
    def test_tcp_without_port_invalid(self):
        from core.forms import AGVForm
        form = AGVForm(data={
            "name": "bad-tcp", "description": "", "vpn_ip": "10.0.0.20",
            "hostname": "", "enabled": "on",
            "probe_method": "tcp", "probe_port": "",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("probe_port", form.errors)

    def test_icmp_without_port_ok(self):
        from core.forms import AGVForm
        form = AGVForm(data={
            "name": "good-icmp", "description": "", "vpn_ip": "10.0.0.21",
            "hostname": "", "enabled": "on",
            "probe_method": "icmp", "probe_port": "",
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_http_without_port_invalid(self):
        from core.forms import AGVForm
        form = AGVForm(data={
            "name": "bad-http", "description": "", "vpn_ip": "10.0.0.22",
            "hostname": "", "enabled": "on",
            "probe_method": "http", "probe_port": "",
        })
        self.assertFalse(form.is_valid())


# ---------------------------------------------------------------------------
# CRUD views
# ---------------------------------------------------------------------------

class AGVCrudViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ops", password="ops12345")

    def setUp(self):
        self.client.login(username="ops", password="ops12345")

    def test_index_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse("core:index"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_index_empty_state(self):
        r = self.client.get(reverse("core:index"))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"No AGVs yet", r.content)

    def test_create_and_list(self):
        r = self.client.post(reverse("core:agv_add"), {
            "name": "bot-1", "description": "first",
            "vpn_ip": "10.99.0.1", "hostname": "",
            "enabled": "on", "probe_method": "icmp", "probe_port": "",
        })
        self.assertEqual(r.status_code, 302)
        agv = AGV.objects.get(name="bot-1")
        self.assertEqual(agv.vpn_ip, "10.99.0.1")
        r = self.client.get(reverse("core:index"))
        self.assertIn(b"bot-1", r.content)

    def test_update(self):
        agv = AGV.objects.create(name="bot-update", vpn_ip="10.99.0.2",
                                  probe_method="icmp")
        r = self.client.post(reverse("core:agv_edit", args=[agv.uuid]), {
            "name": "bot-update", "description": "edited",
            "vpn_ip": "10.99.0.3", "hostname": "renamed.lan",
            "enabled": "on", "probe_method": "tcp", "probe_port": "22",
        })
        self.assertEqual(r.status_code, 302)
        agv.refresh_from_db()
        self.assertEqual(agv.vpn_ip, "10.99.0.3")
        self.assertEqual(agv.probe_method, "tcp")
        self.assertEqual(agv.probe_port, 22)

    def test_delete(self):
        agv = AGV.objects.create(name="bot-del", vpn_ip="10.99.0.4")
        r = self.client.post(reverse("core:agv_delete", args=[agv.uuid]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(AGV.objects.filter(pk=agv.pk).exists())

    def test_detail_renders(self):
        agv = AGV.objects.create(name="bot-detail", vpn_ip="10.99.0.5")
        r = self.client.get(reverse("core:agv_detail", args=[agv.uuid]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"bot-detail", r.content)
        self.assertIn(b"Uptime timeline", r.content)


# ---------------------------------------------------------------------------
# JSON endpoints
# ---------------------------------------------------------------------------

class JsonEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ops", password="ops12345")
        cls.agv = AGV.objects.create(name="json-bot", vpn_ip="10.99.1.1")
        now = timezone.now()
        UptimeSample.objects.bulk_create([
            UptimeSample(
                agv=cls.agv, timestamp=now - timedelta(minutes=i),
                is_online=(i % 3 != 0),  # ~2/3 online
                response_ms=1.0 if i % 3 != 0 else None,
                source=UptimeSample.Source.PROBE,
            )
            for i in range(60)
        ])

    def setUp(self):
        self.client.login(username="ops", password="ops12345")

    def test_samples_default_range(self):
        r = self.client.get(reverse("core:agv_samples_json", args=[self.agv.uuid]))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["agv"]["name"], "json-bot")
        self.assertEqual(data["total"], 60)
        # 20/60 are offline by the (i%3==0) rule -> 40/60 online -> 66.7%
        self.assertAlmostEqual(data["uptime_pct"], 100 * 40 / 60, places=1)

    def test_samples_bad_range(self):
        r = self.client.get(
            reverse("core:agv_samples_json", args=[self.agv.uuid]) + "?range=banana")
        self.assertEqual(r.status_code, 400)

    def test_samples_custom_window_narrows_result(self):
        now = timezone.now()
        t_from = quote((now - timedelta(minutes=10)).isoformat())
        t_to = quote(now.isoformat())
        r = self.client.get(
            reverse("core:agv_samples_json", args=[self.agv.uuid])
            + f"?from={t_from}&to={t_to}")
        self.assertEqual(r.status_code, 200)
        # We have 60 samples spaced 1 min apart; ~11 should fall in last 10 minutes
        self.assertLessEqual(r.json()["total"], 12)
        self.assertGreaterEqual(r.json()["total"], 8)

    def test_samples_unknown_uuid_404(self):
        r = self.client.get(reverse("core:agv_samples_json",
                                    args=[uuid_lib.uuid4()]))
        self.assertEqual(r.status_code, 404)

    def test_samples_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse("core:agv_samples_json", args=[self.agv.uuid]))
        self.assertEqual(r.status_code, 302)

    def test_daily_returns_n_days(self):
        r = self.client.get(reverse("core:agv_daily_json", args=[self.agv.uuid])
                            + "?days=7")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["days"], 7)
        self.assertEqual(len(data["points"]), 7)

    def test_daily_clamps_huge_days(self):
        r = self.client.get(reverse("core:agv_daily_json", args=[self.agv.uuid])
                            + "?days=99999")
        self.assertEqual(r.status_code, 200)
        # Clamped to 365 max
        self.assertEqual(r.json()["days"], 365)


# ---------------------------------------------------------------------------
# Dashboard view
# ---------------------------------------------------------------------------

class DashboardViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="ops", password="ops12345")
        cls.agv = AGV.objects.create(name="dash-bot", vpn_ip="10.99.2.1")
        today = timezone.localdate()
        UptimeDailyAggregate.objects.create(
            agv=cls.agv, date=today,
            samples_total=100, samples_online=95,
            uptime_seconds=95 * 60, longest_outage_seconds=120,
        )

    def setUp(self):
        self.client.login(username="ops", password="ops12345")

    def test_dashboard_renders(self):
        r = self.client.get(reverse("core:dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Uptime heatmap", r.content)
        self.assertIn(b"dash-bot", r.content)

    def test_dashboard_respects_days_param(self):
        r = self.client.get(reverse("core:dashboard") + "?days=14")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"14 days ending", r.content)


# ---------------------------------------------------------------------------
# Aggregation management command
# ---------------------------------------------------------------------------

class AggregateUptimeTests(TestCase):
    def test_aggregates_day_from_samples(self):
        agv = AGV.objects.create(name="agg-bot", vpn_ip="10.99.3.1")
        # Seed 60 samples spread evenly across "today", 70% online.
        today = timezone.localdate()
        anchor = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        samples = []
        for i in range(60):
            samples.append(UptimeSample(
                agv=agv,
                timestamp=anchor - timedelta(minutes=i),
                is_online=(i % 10 < 7),
                response_ms=1.0 if i % 10 < 7 else None,
                source=UptimeSample.Source.PROBE,
            ))
        UptimeSample.objects.bulk_create(samples)

        call_command("aggregate_uptime", "--date", today.isoformat())

        agg = UptimeDailyAggregate.objects.get(agv=agv, date=today)
        self.assertEqual(agg.samples_total, 60)
        self.assertEqual(agg.samples_online, 42)
        self.assertAlmostEqual(agg.uptime_pct, 70.0)

    def test_aggregates_no_data_is_noop(self):
        # No AGVs and no samples — aggregate_uptime should run cleanly.
        call_command("aggregate_uptime", "--days", "1")
        self.assertEqual(UptimeDailyAggregate.objects.count(), 0)


# ---------------------------------------------------------------------------
# Watchdog probe loop (TCP path — exercisable without raw sockets)
# ---------------------------------------------------------------------------

class WatchdogTests(TransactionTestCase):
    """Watchdog DB writes happen via sync_to_async from an asyncio loop, which
    runs in a separate thread. Django's regular TestCase wraps the test in a
    transaction the other thread can't see (and the inner transaction.atomic
    in the aggregator deadlocks on SQLite). TransactionTestCase avoids both
    by truncating tables between tests instead."""

    def setUp(self):
        # Stand up a TCP listener on a random port so the probe sees something.
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self._stop = threading.Event()

        def accept_loop():
            self.srv.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    c, _ = self.srv.accept()
                    c.close()
                except (socket.timeout, OSError):
                    if self._stop.is_set():
                        return

        self._thread = threading.Thread(target=accept_loop, daemon=True)
        self._thread.start()

    def tearDown(self):
        self._stop.set()
        self.srv.close()

    def test_probe_cycle_records_states(self):
        online = AGV.objects.create(
            name="online", vpn_ip="127.0.0.1",
            probe_method="tcp", probe_port=self.port,
        )
        offline = AGV.objects.create(
            name="offline", vpn_ip="127.0.0.2",
            probe_method="tcp", probe_port=1,  # nothing listening
        )
        disabled = AGV.objects.create(
            name="paused", vpn_ip="127.0.0.3",
            probe_method="tcp", probe_port=22, enabled=False,
        )

        call_command("run_watchdog", "--once", "--timeout-ms", "500")

        online.refresh_from_db()
        offline.refresh_from_db()
        disabled.refresh_from_db()

        self.assertEqual(online.last_state, AGV.State.ONLINE)
        self.assertIsNotNone(online.last_response_ms)
        self.assertIsNotNone(online.last_seen_at)

        self.assertEqual(offline.last_state, AGV.State.OFFLINE)
        self.assertEqual(disabled.last_state, AGV.State.UNKNOWN)

        # online + offline each produced one sample; disabled was skipped
        self.assertEqual(UptimeSample.objects.count(), 2)
