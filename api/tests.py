"""Tests for the heartbeat API."""
import json

from django.test import TestCase
from django.urls import reverse

from core.models import AGV, UptimeSample


class HeartbeatTests(TestCase):
    def setUp(self):
        self.agv = AGV.objects.create(name="hb-bot", vpn_ip="10.99.4.1")
        self.url = reverse("api:heartbeat")

    def test_valid_heartbeat_accepted(self):
        r = self.client.post(
            self.url,
            data=json.dumps({"uuid": str(self.agv.uuid), "rtt_ms": 2.5,
                             "note": "self-test"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 202)

        self.agv.refresh_from_db()
        self.assertEqual(self.agv.last_state, AGV.State.ONLINE)
        self.assertIsNotNone(self.agv.last_seen_at)
        self.assertAlmostEqual(self.agv.last_response_ms, 2.5)

        sample = UptimeSample.objects.get(agv=self.agv)
        self.assertEqual(sample.source, UptimeSample.Source.HEARTBEAT)
        self.assertTrue(sample.is_online)
        self.assertEqual(sample.note, "self-test")

    def test_heartbeat_without_rtt_or_note(self):
        r = self.client.post(
            self.url,
            data=json.dumps({"uuid": str(self.agv.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 202)
        self.agv.refresh_from_db()
        self.assertEqual(self.agv.last_state, AGV.State.ONLINE)
        self.assertIsNone(self.agv.last_response_ms)

    def test_unknown_uuid_returns_404(self):
        r = self.client.post(
            self.url,
            data=json.dumps({"uuid": "00000000-0000-0000-0000-000000000000"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 404)
        self.assertEqual(UptimeSample.objects.count(), 0)

    def test_missing_uuid_returns_400(self):
        r = self.client.post(
            self.url,
            data=json.dumps({"rtt_ms": 1.0}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_invalid_uuid_format_returns_400(self):
        r = self.client.post(
            self.url,
            data=json.dumps({"uuid": "not-a-uuid"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)

    def test_heartbeat_no_session_required(self):
        # AGVs don't have Django sessions — the endpoint must accept
        # unauthenticated requests (UUID is the secret).
        # This is implicitly tested above, but make it explicit.
        self.client.logout()  # no-op since we never logged in
        r = self.client.post(
            self.url,
            data=json.dumps({"uuid": str(self.agv.uuid)}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 202)
