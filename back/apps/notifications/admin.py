from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('kind', 'user', 'workspace', 'created_at', 'read_at')
    list_filter = ('kind',)
    raw_id_fields = ('user', 'workspace', 'project', 'post')
