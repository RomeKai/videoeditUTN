from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """``notifications.ts`` · Notification: ``type`` is the kind; the ids say where it leads."""

    type = serializers.CharField(source='kind')
    project_id = serializers.UUIDField(allow_null=True)
    post_id = serializers.UUIDField(allow_null=True)

    class Meta:
        model = Notification
        fields = ['id', 'type', 'title', 'body', 'created_at', 'read_at', 'project_id', 'post_id']
        read_only_fields = fields


class NotificationListSerializer(serializers.Serializer):
    unread_count = serializers.IntegerField()
    results = NotificationSerializer(many=True)
