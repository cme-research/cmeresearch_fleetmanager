"""Compute or refresh UptimeDailyAggregate rows from raw UptimeSample data.

Typical use is automatic, via the watchdog's nightly tick. You can also run
it manually:

    # Recompute yesterday and today (default)
    python manage.py aggregate_uptime

    # Recompute the last N days
    python manage.py aggregate_uptime --days 30

    # Recompute a specific date
    python manage.py aggregate_uptime --date 2026-05-23
"""
from datetime import date as date_cls, datetime, time, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import AGV, UptimeDailyAggregate, UptimeSample


class Command(BaseCommand):
    help = "Recompute UptimeDailyAggregate rows from raw samples."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=2,
                            help="Recompute the last N days (default: 2 — yesterday + today).")
        parser.add_argument("--date", type=str, default=None,
                            help="Recompute one specific date (YYYY-MM-DD). Overrides --days.")

    def handle(self, *args, **opts):
        if opts["date"]:
            try:
                d = date_cls.fromisoformat(opts["date"])
            except ValueError as exc:
                raise CommandError(f"invalid --date: {exc}") from exc
            dates = [d]
        else:
            today = timezone.localdate()
            dates = [today - timedelta(days=i) for i in range(opts["days"])]

        total = 0
        for d in dates:
            total += aggregate_day(d)
        self.stdout.write(f"aggregated {total} (agv, day) rows over {len(dates)} day(s)")


def aggregate_day(d: date_cls) -> int:
    """Compute aggregate rows for every AGV with samples on `d`. Returns row count."""
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(d, time.min), tz)
    day_end = day_start + timedelta(days=1)

    agvs_with_samples = (
        AGV.objects
        .filter(samples__timestamp__gte=day_start,
                samples__timestamp__lt=day_end)
        .distinct()
    )

    n = 0
    with transaction.atomic():
        for agv in agvs_with_samples:
            samples = list(
                agv.samples
                .filter(timestamp__gte=day_start, timestamp__lt=day_end)
                .order_by("timestamp")
                .values_list("timestamp", "is_online")
            )
            if not samples:
                continue

            total = len(samples)
            online = sum(1 for _, is_on in samples if is_on)

            # Walk samples to derive uptime_seconds and longest_outage_seconds.
            # Treat each sample as covering [prev_ts, this_ts]; the first sample
            # covers from day_start.
            uptime_s = 0
            longest_outage_s = 0
            current_outage_s = 0
            prev_ts = day_start
            prev_state = None
            for ts, is_on in samples:
                gap_s = int((ts - prev_ts).total_seconds())
                # Attribute the gap to the previous sample's state.
                # For the very first sample there is no "previous state", so we
                # use the current sample's state as the best available guess.
                state_for_gap = prev_state if prev_state is not None else is_on
                if state_for_gap:
                    uptime_s += gap_s
                    if current_outage_s > longest_outage_s:
                        longest_outage_s = current_outage_s
                    current_outage_s = 0
                else:
                    current_outage_s += gap_s
                prev_ts, prev_state = ts, is_on
            # Tail outage check
            if current_outage_s > longest_outage_s:
                longest_outage_s = current_outage_s

            UptimeDailyAggregate.objects.update_or_create(
                agv=agv,
                date=d,
                defaults=dict(
                    samples_total=total,
                    samples_online=online,
                    uptime_seconds=uptime_s,
                    longest_outage_seconds=longest_outage_s,
                ),
            )
            n += 1
    return n
