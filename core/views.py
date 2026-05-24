from datetime import timedelta, date as date_cls

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from .forms import AGVForm
from .models import AGV, UptimeDailyAggregate, UptimeSample


RANGE_PRESETS = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


class AGVListView(LoginRequiredMixin, ListView):
    model = AGV
    template_name = "core/agv_list.html"
    context_object_name = "agvs"


class AGVCreateView(LoginRequiredMixin, CreateView):
    model = AGV
    form_class = AGVForm
    template_name = "core/agv_form.html"
    success_url = reverse_lazy("core:index")


class AGVUpdateView(LoginRequiredMixin, UpdateView):
    model = AGV
    form_class = AGVForm
    template_name = "core/agv_form.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    success_url = reverse_lazy("core:index")


class AGVDeleteView(LoginRequiredMixin, DeleteView):
    model = AGV
    template_name = "core/agv_confirm_delete.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    success_url = reverse_lazy("core:index")


class AGVDetailView(LoginRequiredMixin, DetailView):
    model = AGV
    template_name = "core/agv_detail.html"
    slug_field = "uuid"
    slug_url_kwarg = "uuid"
    context_object_name = "agv"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        recent = self.object.samples.order_by("-timestamp")[:20]
        ctx["recent_samples"] = recent
        return ctx


@login_required
@require_GET
def agv_samples_json(request, uuid):
    """Return time-series samples for one AGV.

    Query string:
      range=1d|7d|30d                (default 1d)
      from=ISO-8601, to=ISO-8601     (overrides range when both present)
    """
    agv = get_object_or_404(AGV, uuid=uuid)

    now = timezone.now()
    from_str = request.GET.get("from")
    to_str = request.GET.get("to")

    if from_str and to_str:
        t_from = parse_datetime(from_str)
        t_to = parse_datetime(to_str)
        if t_from is None or t_to is None:
            return JsonResponse({"error": "invalid from/to"}, status=400)
        if timezone.is_naive(t_from):
            t_from = timezone.make_aware(t_from)
        if timezone.is_naive(t_to):
            t_to = timezone.make_aware(t_to)
    else:
        rng = request.GET.get("range", "1d")
        delta = RANGE_PRESETS.get(rng)
        if delta is None:
            return JsonResponse(
                {"error": f"unknown range {rng!r}, expected one of {list(RANGE_PRESETS)}"},
                status=400,
            )
        t_to = now
        t_from = now - delta

    qs = (
        agv.samples
        .filter(timestamp__gte=t_from, timestamp__lte=t_to)
        .order_by("timestamp")
        .values("timestamp", "is_online", "response_ms", "source")
    )

    samples = [
        {
            "t": s["timestamp"].isoformat(),
            "online": 1 if s["is_online"] else 0,
            "rtt": s["response_ms"],
            "source": s["source"],
        }
        for s in qs
    ]

    total = len(samples)
    online_count = sum(1 for s in samples if s["online"])
    uptime_pct = (100.0 * online_count / total) if total else None

    return JsonResponse({
        "agv": {"name": agv.name, "uuid": str(agv.uuid)},
        "from": t_from.isoformat(),
        "to": t_to.isoformat(),
        "samples": samples,
        "total": total,
        "online": online_count,
        "uptime_pct": uptime_pct,
        "last_seen_at": agv.last_seen_at.isoformat() if agv.last_seen_at else None,
    })


@login_required
@require_GET
def agv_daily_json(request, uuid):
    """Return daily uptime aggregates for one AGV (default: last 30 days)."""
    agv = get_object_or_404(AGV, uuid=uuid)
    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        return JsonResponse({"error": "days must be an integer"}, status=400)
    days = max(1, min(days, 365))

    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    aggs = {
        a.date: a
        for a in agv.daily_aggregates.filter(date__gte=start, date__lte=today)
    }

    points = []
    for i in range(days):
        d = start + timedelta(days=i)
        a = aggs.get(d)
        points.append({
            "date": d.isoformat(),
            "uptime_pct": a.uptime_pct if a else None,
            "samples": a.samples_total if a else 0,
            "longest_outage_seconds": a.longest_outage_seconds if a else None,
        })

    return JsonResponse({
        "agv": {"name": agv.name, "uuid": str(agv.uuid)},
        "days": days,
        "points": points,
    })


@login_required
def dashboard(request):
    """Multi-AGV uptime dashboard: heatmap of last N days × all AGVs."""
    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        days = 30
    days = max(7, min(days, 90))

    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    date_range = [start + timedelta(days=i) for i in range(days)]

    agvs = list(AGV.objects.all())
    agg_by_agv_day: dict[tuple[int, date_cls], UptimeDailyAggregate] = {
        (a.agv_id, a.date): a
        for a in UptimeDailyAggregate.objects.filter(
            date__gte=start, date__lte=today
        )
    }

    rows = []
    for agv in agvs:
        cells = []
        running_total_pct = 0.0
        running_count = 0
        for d in date_range:
            a = agg_by_agv_day.get((agv.pk, d))
            pct = a.uptime_pct if a else None
            cells.append({"date": d, "pct": pct, "agg": a})
            if pct is not None:
                running_total_pct += pct
                running_count += 1
        avg_pct = (running_total_pct / running_count) if running_count else None
        rows.append({"agv": agv, "cells": cells, "avg_pct": avg_pct})

    return render(request, "core/dashboard.html", {
        "rows": rows,
        "date_range": date_range,
        "days": days,
        "today": today,
    })
