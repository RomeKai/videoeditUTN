from django import forms
from django.contrib import admin

from .models import SocialProfile


PROVIDER_LOCKED = (
    "Este perfil ya existe en {provider}: sus publicaciones salen por ahí. Para pasar el espacio a otro "
    "proveedor, desconectá sus redes y borrá este perfil; el próximo «Conectar» lo crea con SOCIAL_PROVIDER."
)


class SocialProfileForm(forms.ModelForm):
    """
    `provider_ref` can be a credential (Ayrshare's Profile-Key): the form never
    shows it, not even to staff who may only view. It can only be replaced: a
    key typed here is saved, an empty field keeps the one there.

    The provider of a profile that exists at the provider (it has its ref)
    does not change: its ref only means something there. A row without one
    (before the first «Conectar») may take either provider.
    """

    new_provider_ref = forms.CharField(
        label='Nueva referencia', required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'off'}),
        help_text='La Profile-Key de Ayrshare o el usuario de Upload-Post. Se guarda sin mostrarse. Vacío deja la que está.',
    )

    class Meta:
        model = SocialProfile
        exclude = ('provider_ref',)

    def clean_provider(self):
        provider = self.cleaned_data['provider']
        instance = self.instance
        if instance.pk and instance.provider_ref and provider != instance.provider and not (self.data.get('new_provider_ref') or '').strip():
            raise forms.ValidationError(PROVIDER_LOCKED.format(provider=instance.get_provider_display()))
        return provider

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
