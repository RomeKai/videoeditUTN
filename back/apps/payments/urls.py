from django.urls import path

from .views import (
    EstimateView, PricingView, PublicPlanListView, TransactionExportView, TransactionListView, WalletUsageView,
    WalletView,
)

urlpatterns = [
    path('plans/', PublicPlanListView.as_view(), name='public-plans'),
    path('pricing/', PricingView.as_view(), name='pricing'),
    path('pricing/estimate/', EstimateView.as_view(), name='pricing-estimate'),
    path('wallet/', WalletView.as_view(), name='wallet'),
    path('wallet/usage/', WalletUsageView.as_view(), name='wallet-usage'),
    path('transactions/', TransactionListView.as_view(), name='transaction-list'),
    path('transactions/export/', TransactionExportView.as_view(), name='transaction-export'),
]
