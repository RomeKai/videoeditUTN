from django.apps import AppConfig


class VideosConfig(AppConfig):
    name = 'apps.videos'
    label = 'videos'

    def ready(self):
        from . import checks, signals  # noqa: F401  (registers the checks and receivers)
