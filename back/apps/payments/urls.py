from django.urls import path

from .views import (
    EstimateView, PricingView, PublicPlanListView, TransactionExportView, TransactionListView, WalletUsageView,
    WalletView,
)
from .views_purchases import SimulatePaymentView, TokenPackagesView, TokenPurchaseCreateView, TokenPurchaseDetailView
from .views_subscription import ChangePlanView, SubscriptionView

urlpatterns = [
    path('plans/', PublicPlanListView.as_view(), name='public-plans'),
    path('pricing/', PricingView.as_view(), name='pricing'),
    path('pricing/estimate/', EstimateView.as_view(), name='pricing-estimate'),
    path('wallet/', WalletView.as_view(), name='wallet'),
    path('wallet/usage/', WalletUsageView.as_view(), name='wallet-usage'),
    path('transactions/', TransactionListView.as_view(), name='transaction-list'),
    path('transactions/export/', TransactionExportView.as_view(), name='transaction-export'),
    path('subscription/', SubscriptionView.as_view(), name='subscription'),
    path('subscription/change/', ChangePlanView.as_view(), name='subscription-change'),
    path('token-packages/', TokenPackagesView.as_view(), name='token-packages'),
    path('token-purchases/', TokenPurchaseCreateView.as_view(), name='token-purchases'),
    path('token-purchases/<uuid:payment_id>/', TokenPurchaseDetailView.as_view(), name='token-purchase-detail'),
    path('token-purchases/<uuid:payment_id>/simulate/', SimulatePaymentView.as_view(), name='token-purchase-simulate'),
]
