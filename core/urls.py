from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.AGVListView.as_view(), name="index"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("agvs/add/", views.AGVCreateView.as_view(), name="agv_add"),
    path("agvs/<uuid:uuid>/", views.AGVDetailView.as_view(), name="agv_detail"),
    path("agvs/<uuid:uuid>/edit/", views.AGVUpdateView.as_view(), name="agv_edit"),
    path("agvs/<uuid:uuid>/delete/", views.AGVDeleteView.as_view(), name="agv_delete"),
    path("agvs/<uuid:uuid>/samples.json",
         views.agv_samples_json, name="agv_samples_json"),
    path("agvs/<uuid:uuid>/daily.json",
         views.agv_daily_json, name="agv_daily_json"),
]
