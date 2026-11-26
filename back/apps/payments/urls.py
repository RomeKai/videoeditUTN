from django.urls import path

from .views import PublicPlanListView

urlpatterns = [
    path('plans/', PublicPlanListView.as_view(), name='public-plans'),
]
