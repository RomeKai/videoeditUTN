# payments serializers
from rest_framework import serializers

from .models import SubscriptionPlan


class PublicPlanSerializer(serializers.ModelSerializer):
    """What the pricing page shows of a plan (front contract `Plan`)."""

    class Meta:
        model = SubscriptionPlan
        fields = [
            'id',
            'name',
            'max_video_duration_seconds',
            'max_resolution',
            'has_watermark',
            'allow_scheduling',
            'allow_crossposting',
            'has_seo_optimization',
            'has_thumbnail_engine',
            'base_discount_rate',
            'max_members',
            'is_active',
            'price_usd_monthly',
            'description',
            'sort_order',
            'is_recommended',
        ]
        read_only_fields = fields
