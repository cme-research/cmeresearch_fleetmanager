"""Probe every enabled AGV on a schedule and record UptimeSample rows.

Run as:
    python manage.py run_watchdog [--once] [--interval 60]

The watchdog reloads the AGV list each tick, so adding / pausing / deleting
AGVs in the UI takes effect on the next cycle without restart.
"""
import asyncio
import logging
import signal
import time
from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.management.commands.aggregate_uptime import aggregate_day
from core.models import AGV, UptimeSample

log = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    agv_id: int
    is_online: bool
    response_ms: float | None
    note: str = ""


class Command(BaseCommand):
    help = "Continuously probe AGVs and write UptimeSample rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval", type=int, default=None,
            help="Seconds between probe cycles (default: settings.PROBE_INTERVAL_S).",
        )
        parser.add_argument(
            "--timeout-ms", type=int, default=None,
            help="Per-probe timeout in ms (default: settings.PROBE_TIMEOUT_MS).",
        )
        parser.add_argument(
            "--once", action="store_true",
            help="Run a single probe cycle and exit (useful for tests / cron).",
        )

    def handle(self, *args, **opts):
        interval = opts["interval"] or settings.PROBE_INTERVAL_S
        timeout_ms = opts["timeout_ms"] or settings.PROBE_TIMEOUT_MS
        once = opts["once"]

        log.info("watchdog starting: interval=%ss timeout=%sms once=%s",
                 interval, timeout_ms, once)

        try:
            asyncio.run(_run(interval=interval, timeout_ms=timeout_ms, once=once))
        except KeyboardInterrupt:
            log.info("watchdog stopped by user")


async def _run(*, interval: int, timeout_ms: int, once: bool):
    from django.utils import timezone as djtz
    stop = asyncio.Event()

    def _on_signal():
        log.info("signal received, stopping after this cycle")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass

    last_aggregated_date = None

    while not stop.is_set():
        cycle_started = time.monotonic()
        try:
            await _probe_cycle(timeout_ms=timeout_ms)
        except Exception:
            log.exception("probe cycle failed")

        # Aggregation: on first cycle (last_aggregated_date is None) or when the
        # local date has rolled over since last aggregation, refresh yesterday
        # and today's daily aggregates. Cheap idempotent operation.
        today = djtz.localdate()
        if last_aggregated_date != today:
            try:
                await _aggregate_recent_days(today)
                last_aggregated_date = today
            except Exception:
                log.exception("aggregation failed")

        if once:
            return

        elapsed = time.monotonic() - cycle_started
        sleep_for = max(0.0, interval - elapsed)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass


@sync_to_async
def _aggregate_recent_days(today):
    from datetime import timedelta
    rows = aggregate_day(today) + aggregate_day(today - timedelta(days=1))
    log.info("aggregated %d daily rows (today + yesterday)", rows)


async def _probe_cycle(*, timeout_ms: int):
    agvs = await _load_enabled_agvs()
    if not agvs:
        log.debug("no enabled AGVs to probe")
        return

    log.info("probing %d AGV(s)", len(agvs))
    results: list[ProbeResult] = []

    # Group by probe method for efficient batch handling.
    icmp_targets = [a for a in agvs if a.probe_method == AGV.ProbeMethod.ICMP and a.vpn_ip]
    tcp_targets = [a for a in agvs if a.probe_method == AGV.ProbeMethod.TCP and a.vpn_ip and a.probe_port]
    skipped = [a for a in agvs
               if a not in icmp_targets and a not in tcp_targets]

    if icmp_targets:
        results.extend(await _probe_icmp(icmp_targets, timeout_ms))
    if tcp_targets:
        results.extend(await asyncio.gather(*(
            _probe_tcp(a, timeout_ms) for a in tcp_targets
        )))
    for a in skipped:
        results.append(ProbeResult(
            agv_id=a.pk,
            is_online=False,
            response_ms=None,
            note="no probe target (vpn_ip / probe_port missing)",
        ))

    await _persist_results(results)


@sync_to_async
def _load_enabled_agvs():
    return list(AGV.objects.filter(enabled=True))


async def _probe_icmp(agvs, timeout_ms):
    # icmplib.async_multiping pings all targets in parallel via one event loop.
    from icmplib import async_multiping

    addrs = [a.vpn_ip for a in agvs]
    hosts = await async_multiping(
        addresses=addrs,
        count=1,
        interval=0.05,
        timeout=timeout_ms / 1000.0,
        privileged=False,  # uses unprivileged ICMP sockets if available
    )
    results = []
    for agv, host in zip(agvs, hosts):
        rtt = host.avg_rtt if host.is_alive else None
        results.append(ProbeResult(
            agv_id=agv.pk,
            is_online=host.is_alive,
            response_ms=rtt,
            note="" if host.is_alive else "icmp no reply",
        ))
    return results


async def _probe_tcp(agv, timeout_ms):
    start = time.monotonic()
    try:
        fut = asyncio.open_connection(agv.vpn_ip, agv.probe_port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout_ms / 1000.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        rtt = (time.monotonic() - start) * 1000.0
        return ProbeResult(agv_id=agv.pk, is_online=True, response_ms=rtt)
    except (OSError, asyncio.TimeoutError) as exc:
        return ProbeResult(
            agv_id=agv.pk, is_online=False, response_ms=None,
            note=f"tcp: {exc.__class__.__name__}",
        )


@sync_to_async
def _persist_results(results):
    now = timezone.now()
    samples = [
        UptimeSample(
            agv_id=r.agv_id,
            timestamp=now,
            is_online=r.is_online,
            response_ms=r.response_ms,
            source=UptimeSample.Source.PROBE,
            note=r.note,
        )
        for r in results
    ]
    UptimeSample.objects.bulk_create(samples)

    online_ids = [r.agv_id for r in results if r.is_online]
    offline_ids = [r.agv_id for r in results if not r.is_online]

    if online_ids:
        # Update last_seen_at + last_state + last_response_ms per AGV.
        # We loop one update per AGV because last_response_ms varies; for
        # ~50 AGVs that's cheap.
        for r in results:
            if r.is_online:
                AGV.objects.filter(pk=r.agv_id).update(
                    last_seen_at=now,
                    last_state=AGV.State.ONLINE,
                    last_response_ms=r.response_ms,
                )
    if offline_ids:
        AGV.objects.filter(pk__in=offline_ids).update(
            last_state=AGV.State.OFFLINE,
        )

    log.info("cycle done: %d online, %d offline", len(online_ids), len(offline_ids))
