from django.urls import path
from .views import RoutePlannerView

urlpatterns = [
    path('route-planner/', RoutePlannerView.as_view(), name='route-planner'),
]