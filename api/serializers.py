from rest_framework import serializers


class HeartbeatSerializer(serializers.Serializer):
    """Schema for AGV heartbeat POST."""
    uuid = serializers.UUIDField()
    # rtt_ms is optional — the AGV may report its own perceived round-trip time
    # to e.g. an MQTT broker, the ROS master, or just leave it null.
    rtt_ms = serializers.FloatField(required=False, allow_null=True)
    # AGVs may post a timestamp slightly in the past if they were briefly
    # disconnected; ignore client-provided timestamps to keep the server clock
    # authoritative, but accept the field for forwards compatibility.
    timestamp = serializers.DateTimeField(required=False, allow_null=True)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)
