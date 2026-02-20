from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard_overview, name="dashboard_overview"),
    path("dashboard/reports/", views.dashboard_reports, name="dashboard_reports"),
    path("dashboard/profile/", views.dashboard_profile, name="dashboard_profile"),
    path("dashboard/settings/", views.dashboard_settings, name="dashboard_settings"),
]
