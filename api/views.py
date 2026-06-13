from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import AGV, UptimeSample

from .serializers import HeartbeatSerializer


class HeartbeatView(APIView):
    """Accept a push heartbeat from an AGV.

    AGVs prove identity via the UUID in the request body. The UUID is treated
    as a shared secret — it must be kept private. Run the server behind a VPN
    and use HTTPS in production.

    Request:
        POST /api/heartbeat/
        Content-Type: application/json
        {"uuid": "...", "rtt_ms": 1.2, "note": ""}

    Response:
        202 Accepted on success
        404 if the UUID does not match any AGV
        400 on malformed body
    """
    # The AGV does not have a Django session; the UUID itself authenticates.
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = HeartbeatSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        try:
            agv = AGV.objects.get(uuid=data["uuid"])
        except AGV.DoesNotExist:
            return Response({"detail": "unknown AGV uuid"},
                            status=status.HTTP_404_NOT_FOUND)

        now = timezone.now()
        rtt = data.get("rtt_ms")
        note = data.get("note", "")

        UptimeSample.objects.create(
            agv=agv,
            timestamp=now,
            is_online=True,
            response_ms=rtt,
            source=UptimeSample.Source.HEARTBEAT,
            note=note,
        )
        AGV.objects.filter(pk=agv.pk).update(
            last_seen_at=now,
            last_state=AGV.State.ONLINE,
            last_response_ms=rtt,
        )
        return Response({"status": "ok", "received_at": now.isoformat()},
                        status=status.HTTP_202_ACCEPTED)
