"""
Gradual enablement of AI Core V2 (AICORE-9, runbook §5.1 step 6 and §5.3).

- ``AI_CORE_V2_ENABLED`` is the master switch and the kill switch: ``False``
  sends every project to the legacy path, whatever the allowlist says.
- ``AI_CORE_V2_ALLOWLIST`` holds the user ids (of ``VideoProject.uploaded_by``)
  that get V2 while the master switch is on. Empty means everyone.

Each stage asks again when it runs, as before: flipping the switch applies to
the next stage of a project already in flight, and the usage rows record the
path that actually ran (``pipeline_version``).
"""

from typing import FrozenSet

from django.conf import settings


def v2_allowlist() -> FrozenSet[str]:
    raw = getattr(settings, "AI_CORE_V2_ALLOWLIST", None) or ()
    if isinstance(raw, str):
        raw = raw.split(",")
    return frozenset(str(item).strip().lower() for item in raw if str(item).strip())


def v2_enabled_for(project) -> bool:
    """True when this project's AI stages run on V2."""
    if not getattr(settings, "AI_CORE_V2_ENABLED", False):
        return False
    allowlist = v2_allowlist()
    if not allowlist:
        return True
    uploader = getattr(project, "uploaded_by_id", None)
    return uploader is not None and str(uploader).lower() in allowlist
