from django.contrib import admin

from .models import AGV, UptimeSample


@admin.register(AGV)
class AGVAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "vpn_ip",
        "enabled",
        "probe_method",
        "last_state",
        "last_seen_at",
    )
    list_filter = ("enabled", "probe_method", "last_state")
    search_fields = ("name", "vpn_ip", "hostname", "uuid")
    readonly_fields = ("uuid", "created_at", "updated_at",
                       "last_seen_at", "last_state", "last_response_ms")
    fieldsets = (
        (None, {"fields": ("name", "description", "enabled")}),
        ("Network", {"fields": ("vpn_ip", "hostname", "probe_method", "probe_port")}),
        ("Identity", {"fields": ("uuid",)}),
        ("Status (read-only)", {"fields": ("last_state", "last_seen_at",
                                            "last_response_ms",
                                            "created_at", "updated_at")}),
    )


@admin.register(UptimeSample)
class UptimeSampleAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "agv", "is_online", "response_ms", "source", "note")
    list_filter = ("is_online", "source", "agv")
    search_fields = ("agv__name", "note")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
