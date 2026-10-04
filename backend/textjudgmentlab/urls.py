from django.urls import path

from .views import LabAccessAPIView, LabJudgmentAPIView

app_name = "textjudgmentlab"

urlpatterns = [
    path("access", LabAccessAPIView.as_view(), name="access"),
    path("judgments", LabJudgmentAPIView.as_view(), name="judgments"),
]
