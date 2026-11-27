# payments serializers
from rest_framework import serializers

from .models import RESOLUTION_CHOICES, SubscriptionPlan, Transaction
from .services.movements import KIND_CHOICES, kind_of, parse_month


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
            'allow_social_accounts',
            'base_discount_rate',
            'max_members',
            'is_active',
            'price_usd_monthly',
            'description',
            'sort_order',
            'is_recommended',
        ]
        read_only_fields = fields


MONTH_ERROR = "Mandá el mes como AAAA-MM."


class MonthField(serializers.CharField):
    """``YYYY-MM`` -> ``(year, month)``."""

    def to_internal_value(self, data):
        month = parse_month(super().to_internal_value(data))
        if month is None:
            raise serializers.ValidationError(MONTH_ERROR)
        return month


class UsageQuerySerializer(serializers.Serializer):
    month = MonthField(required=False)


class TransactionsQuerySerializer(serializers.Serializer):
    """Filters of the movements (contract `TransactionsQuery`); `page` and `page_size` go to the paginator."""

    kind = serializers.ChoiceField(choices=KIND_CHOICES, required=False)
    month = MonthField(required=False)
    project = serializers.UUIDField(required=False)


class TransactionSerializer(serializers.ModelSerializer):
    """A wallet movement (front contract `Transaction`). Reads the rows of movements.with_details."""

    kind = serializers.SerializerMethodField()
    created_by_detail = serializers.SerializerMethodField()
    project = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            'id', 'wallet', 'created_by', 'amount', 'reserved_amount', 'transaction_type', 'status', 'description',
            'created_at', 'settled_at', 'kind', 'created_by_detail', 'project', 'category',
        ]
        read_only_fields = fields

    def get_kind(self, txn) -> str:
        return kind_of(txn)

    def get_created_by_detail(self, txn) -> dict | None:
        from apps.videos.services.project_activity import user_ref

        return user_ref(txn.created_by) if txn.created_by_id else None

    def get_project(self, txn) -> dict | None:
        return {'id': str(txn.project_id), 'title': txn.project.title} if txn.project_id else None


class EstimateRequestSerializer(serializers.Serializer):
    """Body of ``POST pricing/estimate/`` (contract `EstimateRequest`)."""

    source = serializers.ChoiceField(choices=['file', 'link'])
    # A day: far above any plan, and a number the engine can price.
    duration_seconds = serializers.FloatField(min_value=0, max_value=86400, required=False)
    render_resolution = serializers.ChoiceField(choices=RESOLUTION_CHOICES, required=False)
    use_seo = serializers.BooleanField(required=False, default=False)
    # Part of the contract, for the ETA. The time to the review does not depend on it today.
    max_clips = serializers.IntegerField(min_value=1, required=False)
