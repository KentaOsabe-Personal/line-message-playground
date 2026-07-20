from django.urls import path

from .views import (
    ChannelListAPIView,
    LineLoginAPIView,
    RecipientCollectionAPIView,
    RecipientDetailAPIView,
    SessionAPIView,
)


app_name = "lineaccounts"

urlpatterns = [
    path("session/", SessionAPIView.as_view(), name="session"),
    path("session/line/", LineLoginAPIView.as_view(), name="line-login"),
    path("channels/", ChannelListAPIView.as_view(), name="channels"),
    path("recipients/", RecipientCollectionAPIView.as_view(), name="recipients"),
    path(
        "recipients/<uuid:recipient_id>/",
        RecipientDetailAPIView.as_view(),
        name="recipient-detail",
    ),
]
