from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def health(_request):
    return HttpResponse("ok")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("health/", health),
]
