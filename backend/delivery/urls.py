from django.urls import path

from .views import (
    DeliveryAPIView,
    DeliveryStatusAPIView,
    DeliveryTargetChannelListAPIView,
    DeliveryTargetRecipientListAPIView,
    PreviewAPIView,
)


app_name = "delivery"

urlpatterns = [
    path(
        "targets/channels/",
        DeliveryTargetChannelListAPIView.as_view(),
        name="target-channels",
    ),
    path(
        "targets/channels/<str:channel_id>/recipients/",
        DeliveryTargetRecipientListAPIView.as_view(),
        name="target-recipients",
    ),
    path("preview/", PreviewAPIView.as_view(), name="preview"),
    path("", DeliveryAPIView.as_view(), name="send"),
    path("<str:operation_id>/status/", DeliveryStatusAPIView.as_view(), name="status"),
]
