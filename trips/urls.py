from django.urls import path

from trips import views

urlpatterns = [
    path("plan/", views.plan_trip, name="trips_plan"),
]
