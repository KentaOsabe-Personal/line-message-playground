from django.urls import path

from .views import (
    ChannelCurrentAPIView,
    ChannelHistoryAPIView,
    ChannelOperationAPIView,
    ChannelPreviewAPIView,
    ChannelStateAPIView,
    OperationDetailAPIView,
    TemplateListAPIView,
)

app_name = "linerichmenus"

urlpatterns = [
    path("channels/<uuid:channel_id>/current/", ChannelCurrentAPIView.as_view(), name="current"),
    path("templates/", TemplateListAPIView.as_view(), name="templates"),
    path("channels/<uuid:channel_id>/preview/", ChannelPreviewAPIView.as_view(), name="preview"),
    path("channels/<uuid:channel_id>/state/", ChannelStateAPIView.as_view(), name="state"),
    path(
        "channels/<uuid:channel_id>/operations/",
        ChannelOperationAPIView.as_view(),
        name="operations",
    ),
    path("channels/<uuid:channel_id>/history/", ChannelHistoryAPIView.as_view(), name="history"),
    path(
        "operations/<uuid:operation_id>/", OperationDetailAPIView.as_view(), name="operation-detail"
    ),
]
