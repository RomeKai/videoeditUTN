from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import Payment, Wallet, Transaction, SubscriptionPlan

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'price_usd_monthly', 'sort_order', 'is_recommended', 'is_public', 'is_active',
        'max_video_duration_seconds', 'max_resolution', 'max_members',
        'has_watermark', 'base_discount_rate', 'allow_scheduling',
        'allow_crossposting', 'has_seo_optimization', 'has_thumbnail_engine', 'allow_social_accounts',
    )
    list_filter = ('is_public', 'is_active', 'is_recommended', 'max_resolution', 'has_watermark', 'allow_scheduling', 'has_seo_optimization')
    search_fields = ('name',)

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('workspace', 'available_balance', 'reserved_balance', 'updated_at')
    search_fields = ('workspace__name',)

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'amount', 'transaction_type', 'status', 'description')
    list_filter = ('transaction_type', 'status')
    search_fields = ('id', 'wallet__workspace__name', 'description')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """
    Read only: a payment changes only through its provider (services/payments.py),
    so the admin can never credit tokens or change a plan by hand from here.
    """
    list_display = ('id', 'workspace', 'status', 'tokens', 'plan', 'amount_usd', 'provider', 'created_at', 'settled_at')
    list_filter = ('status', 'provider')
    search_fields = ('id', 'workspace__name', 'provider_reference')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
