from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("health.urls")),
    path("api/account/", include("lineaccounts.urls")),
    path("api/deliveries/", include("delivery.urls")),
]
