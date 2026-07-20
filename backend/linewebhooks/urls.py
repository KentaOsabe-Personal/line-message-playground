from django.urls import path

from .views import WebhookAPIView


app_name = "linewebhooks"

urlpatterns = [
    path("<str:channel_public_key>/", WebhookAPIView.as_view(), name="ingress"),
]
