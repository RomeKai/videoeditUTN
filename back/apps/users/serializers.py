from rest_framework import serializers


class PlanSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()


class WorkspaceSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()
    plan = PlanSummarySerializer(allow_null=True)
    available_balance = serializers.DecimalField(max_digits=20, decimal_places=10)


class MeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    name = serializers.CharField()
    is_email_verified = serializers.BooleanField()
    tour_completed = serializers.BooleanField()
    terms_accepted_at = serializers.DateTimeField(allow_null=True)
    active_workspace_id = serializers.UUIDField(allow_null=True)
    workspaces = WorkspaceSummarySerializer(many=True)
