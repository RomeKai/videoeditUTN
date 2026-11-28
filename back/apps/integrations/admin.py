from django import forms
from django.contrib import admin

from .models import SocialProfile


PROVIDER_LOCKED = (
    "Este perfil ya existe en {provider}: sus publicaciones salen por ahí. Para pasar el espacio a otro "
    "proveedor, desconectá sus redes y borrá este perfil; el próximo «Conectar» lo crea con SOCIAL_PROVIDER."
)
POSTS_IN_FLIGHT = (
    "Este espacio tiene publicaciones en camino o con un resultado incierto: con otro proveedor podrían "
    "salir dos veces. Esperá a que terminen y revisá las inciertas antes de elegir el proveedor."
)


def posts_in_flight(workspace_id):
    """
    Posts of the workspace the worker may still send (QUEUED, PROCESSING, which a retry keeps) or whose
    key may already have published (publish_key_unclear): through another provider they could go out twice.
    """
    from django.db.models import Q

    from apps.videos.models import ScheduledPost

    return ScheduledPost.objects.filter(video_clip__project__workspace_id=workspace_id).filter(
        Q(status__in=(ScheduledPost.Status.QUEUED, ScheduledPost.Status.PROCESSING)) | Q(publish_key_unclear=True),
    ).exists()


class SocialProfileForm(forms.ModelForm):
    """
    `provider_ref` can be a credential (Ayrshare's Profile-Key): the form never
    shows it, not even to staff who may only view. It can only be replaced: a
    key typed here is saved, an empty field keeps the one there.

    The provider of a profile that exists at the provider (it has its ref)
    never changes, not even with a new ref: its posts, retries included, go
    out through it. Moving a workspace is deleting its profile and connecting
    again. A row without a ref (before the first «Conectar») may take either
    provider, unless the workspace has posts in flight or with an unclear key.
    """

    new_provider_ref = forms.CharField(
        label='Nueva referencia', required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'off'}),
        help_text='La Profile-Key de Ayrshare o el usuario de Upload-Post. Se guarda sin mostrarse. Vacío deja la que está.',
    )

    class Meta:
        model = SocialProfile
        exclude = ('provider_ref',)

    def clean(self):
        cleaned = super().clean()
        provider, instance = cleaned.get('provider'), self.instance
        if not provider or provider == instance.provider:
            return cleaned
        if instance.pk and instance.provider_ref:
            self.add_error('provider', PROVIDER_LOCKED.format(provider=instance.get_provider_display()))
            return cleaned
        workspace = cleaned.get('workspace') or getattr(instance, 'workspace', None)
        if workspace is not None and posts_in_flight(workspace.pk):
            self.add_error('provider', POSTS_IN_FLIGHT)
        return cleaned

    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.cleaned_data.get('new_provider_ref'):
            profile.provider_ref = self.cleaned_data['new_provider_ref']
        if commit:
            profile.save()
            self.save_m2m()
        return profile


@admin.register(SocialProfile)
class SocialProfileAdmin(admin.ModelAdmin):
    """The workspace's profile at the publishing provider; its key is never shown (SocialProfileForm)."""

    form = SocialProfileForm
    list_display = ('workspace', 'provider', 'provider_id', 'ready', 'created_at')
    list_filter = ('provider',)
    search_fields = ('workspace__name', 'workspace__id', 'provider_id')
    raw_id_fields = ('workspace',)
    readonly_fields = ('ready', 'created_at', 'updated_at')

    @admin.display(boolean=True, description='Creado en el proveedor')
    def ready(self, profile):
        return profile.is_ready
