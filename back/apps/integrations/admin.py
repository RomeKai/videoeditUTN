from django import forms
from django.contrib import admin

from .models import SocialProfile


class SocialProfileForm(forms.ModelForm):
    """
    `provider_ref` is a credential (Ayrshare's Profile-Key): the form never
    shows it, not even to staff who may only view. It can only be replaced: a
    key typed here is saved, an empty field keeps the one there.
    """

    new_provider_ref = forms.CharField(
        label='Nueva Profile-Key', required=False, strip=True,
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'off'}),
        help_text='Se guarda sin mostrarse. Vacío deja la que está.',
    )

    class Meta:
        model = SocialProfile
        exclude = ('provider_ref',)

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

    @admin.display(boolean=True, description='Tiene Profile-Key')
    def ready(self, profile):
        return profile.is_ready
