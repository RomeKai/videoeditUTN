from django.contrib import admin

from .models import SocialProfile


@admin.register(SocialProfile)
class SocialProfileAdmin(admin.ModelAdmin):
    """
    The workspace's profile at the publishing provider. `provider_ref` is a
    credential (Ayrshare's Profile-Key): it is not listed, and the form is the
    only place staff sees it, to set a key taken from the provider's dashboard.
    """

    list_display = ('workspace', 'provider', 'provider_id', 'ready', 'created_at')
    list_filter = ('provider',)
    search_fields = ('workspace__name', 'workspace__id', 'provider_id')
    raw_id_fields = ('workspace',)
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(boolean=True, description='Creado en el proveedor')
    def ready(self, profile):
        return profile.is_ready
