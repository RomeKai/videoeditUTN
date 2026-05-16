from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import Wallet, Transaction, SubscriptionPlan

@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'max_video_duration_seconds', 'max_resolution', 'has_watermark', 'base_render_discount', 'is_active')
    list_filter = ('is_active', 'max_resolution', 'has_watermark')
    search_fields = ('name',)

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('workspace', 'available_balance', 'reserved_balance', 'updated_at')
    search_fields = ('workspace__name',)

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'amount', 'transaction_type', 'status', 'created_at')
    list_filter = ('transaction_type', 'status')
    search_fields = ('id', 'wallet__workspace__name', 'description')
