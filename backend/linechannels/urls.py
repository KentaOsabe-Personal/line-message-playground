from django.urls import path

from .admin_views import (
    AdminChannelCollectionAPIView,
    AdminChannelConnectionCheckAPIView,
    AdminChannelDeactivationAPIView,
    AdminChannelDeactivationRecheckAPIView,
    AdminChannelDetailAPIView,
    AdminChannelStateAPIView,
)

app_name = "linechannels"

urlpatterns = [
    path("", AdminChannelCollectionAPIView.as_view(), name="admin-collection"),
    path("<uuid:channel_id>/", AdminChannelDetailAPIView.as_view(), name="admin-detail"),
    path("<uuid:channel_id>/state/", AdminChannelStateAPIView.as_view(), name="admin-state"),
    path(
        "<uuid:channel_id>/deactivation/",
        AdminChannelDeactivationAPIView.as_view(),
        name="admin-deactivation",
    ),
    path(
        "<uuid:channel_id>/deactivation/recheck/",
        AdminChannelDeactivationRecheckAPIView.as_view(),
        name="admin-deactivation-recheck",
    ),
    path(
        "<uuid:channel_id>/connection-check/",
        AdminChannelConnectionCheckAPIView.as_view(),
        name="admin-connection-check",
    ),
]
