from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.users.models import NotificationPreference


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


class UpdateMeSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=150, required=False)
    tour_completed = serializers.BooleanField(required=False)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(min_length=8)

    def validate_new_password(self, value):
        validate_password(value, user=self.context['user'])
        return value

    def validate_current_password(self, value):
        if not self.context['user'].check_password(value):
            raise serializers.ValidationError('La contraseña actual no es correcta.')
        return value


class NotificationPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ['project_ready', 'render_finished', 'project_failed', 'post_failed', 'low_balance']
