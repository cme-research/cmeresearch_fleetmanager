from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request):
    """Fleet overview placeholder until the AGV CRUD commit lands."""
    return render(request, "core/index.html")
