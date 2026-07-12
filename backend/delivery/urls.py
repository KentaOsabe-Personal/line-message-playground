from django.urls import path

from .views import DeliveryAPIView, DeliveryStatusAPIView, PreviewAPIView


app_name = "delivery"

urlpatterns = [
    path("preview/", PreviewAPIView.as_view(), name="preview"),
    path("", DeliveryAPIView.as_view(), name="send"),
    path("<str:operation_id>/status/", DeliveryStatusAPIView.as_view(), name="status"),
]

