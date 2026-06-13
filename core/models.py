import uuid

from django.db import models
from django.urls import reverse


class AGV(models.Model):
    class ProbeMethod(models.TextChoices):
        ICMP = "icmp", "ICMP ping"
        TCP = "tcp", "TCP connect"
        HTTP = "http", "HTTP GET"

    class State(models.TextChoices):
        ONLINE = "online", "online"
        OFFLINE = "offline", "offline"
        UNKNOWN = "unknown", "unknown"

    uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    vpn_ip = models.GenericIPAddressField(null=True, blank=True, unique=True)
    hostname = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=True)
    probe_method = models.CharField(
        max_length=10, choices=ProbeMethod.choices, default=ProbeMethod.ICMP
    )
    probe_port = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_state = models.CharField(
        max_length=10, choices=State.choices, default=State.UNKNOWN
    )
    last_response_ms = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("core:agv_detail", kwargs={"uuid": self.uuid})


class UptimeSample(models.Model):
    class Source(models.TextChoices):
        PROBE = "probe", "probe"
        HEARTBEAT = "heartbeat", "heartbeat"

    agv = models.ForeignKey(
        AGV, on_delete=models.CASCADE, related_name="samples"
    )
    timestamp = models.DateTimeField(db_index=True)
    is_online = models.BooleanField()
    response_ms = models.FloatField(null=True, blank=True)
    source = models.CharField(
        max_length=10, choices=Source.choices, default=Source.PROBE
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        indexes = [models.Index(fields=["agv", "timestamp"])]
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.agv.name} {self.timestamp.isoformat()} {'up' if self.is_online else 'down'}"


class UptimeDailyAggregate(models.Model):
    """Pre-aggregated uptime per AGV per day for fast dashboards."""
    agv = models.ForeignKey(
        AGV, on_delete=models.CASCADE, related_name="daily_aggregates"
    )
    date = models.DateField(db_index=True)
    samples_total = models.IntegerField()
    samples_online = models.IntegerField()
    uptime_seconds = models.IntegerField()
    longest_outage_seconds = models.IntegerField()

    class Meta:
        unique_together = ("agv", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.agv.name} {self.date}: {self.uptime_pct:.1f}%"

    @property
    def uptime_pct(self) -> float:
        if not self.samples_total:
            return 0.0
        return 100.0 * self.samples_online / self.samples_total
