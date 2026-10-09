from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("health/", views.health, name="health"),
    path("api/live/", views.api_live, name="api-live"),
    path("api/intraday/<str:symbol>/", views.api_intraday, name="api-intraday"),
    path("api/indicators/<str:symbol>/", views.api_indicators, name="api-indicators"),
    path("api/compare/", views.api_compare, name="api-compare"),
    path("api/predict/", views.api_predict, name="api-predict"),
    path("api/pretrained/live/", views.api_pretrained_live, name="api-pm-live"),
    path("api/pretrained/predict/", views.api_pretrained_predict, name="api-pm-predict"),
    path("api/pretrained/backtest/", views.api_pretrained_backtest, name="api-pm-backtest"),
]
