"""Brand kit rules that touch more than one row or the stored files."""
import logging

from django.db import models, transaction

from apps.users.models import Workspace
from apps.videos.models import BrandKit

from .storage_service import StoredFileCopyError, copy_stored_file

logger = logging.getLogger(__name__)

COPY_NAME_PREFIX = "Copia de "
# Never copied: the copy gets its own, or the value of the request.
_NOT_COPIED = {'id', 'workspace', 'user', 'name', 'is_default', 'created_at'}


class BrandKitCopyError(Exception):
    """A file of the kit could not be copied: nothing was written."""


def lock_workspace_default(workspace_id) -> None:
    """
    Serializes every change of the default kit of a workspace: the caller, in
    a transaction, holds the workspace row until it commits. The partial unique
    constraint ``one_default_brand_kit_per_workspace`` is the last line; with
    this lock no request ever reaches it.
    """
    Workspace.objects.select_for_update().filter(pk=workspace_id).values_list('pk', flat=True).first()


def demote_other_defaults(workspace_id, keep_pk=None) -> None:
    """Unmarks every default kit of the workspace but ``keep_pk``. Call it under ``lock_workspace_default``."""
    BrandKit.objects.filter(workspace_id=workspace_id, is_default=True).exclude(pk=keep_pk).update(is_default=False)


def _delete_copies(copies) -> None:
    """Best effort: a copy that cannot be deleted is logged and left (no row points to it)."""
    for storage, name in copies:
        try:
            storage.delete(name)
        except Exception as exc:
            logger.warning("brand_kit.copy_cleanup_failed error=%s", type(exc).__name__)


def duplicate_brand_kit(kit: BrandKit, user=None) -> BrandKit:
    """
    A copy of ``kit`` in its workspace, named «Copia de {name}», that is never
    the default. Every file (fonts, logo, intro, outro) is copied to a new
    name in its storage, so each kit owns its files: deleting or replacing the
    files of one never touches the other's.

    All or nothing: the files are copied first and the row is written last.
    If a copy or the insert fails, the files already copied are deleted and
    ``BrandKitCopyError`` is raised; no row is left.
    """
    values, copies = {}, []
    try:
        for field in BrandKit._meta.concrete_fields:
            if field.name in _NOT_COPIED:
                continue
            value = getattr(kit, field.name)
            if isinstance(field, models.FileField):
                if not value:
                    values[field.name] = None
                    continue
                storage = value.storage
                new_name = storage.get_available_name(value.name, max_length=field.max_length)
                copy_stored_file(storage, value.name, new_name)
                copies.append((storage, new_name))
                value = new_name
            values[field.name] = value
        with transaction.atomic():
            copy = BrandKit.objects.create(
                workspace_id=kit.workspace_id,
                user=user,
                name=f"{COPY_NAME_PREFIX}{kit.name}"[:BrandKit._meta.get_field('name').max_length],
                is_default=False,
                **values,
            )
    except Exception as exc:
        _delete_copies(copies)
        logger.warning(
            "brand_kit.duplicate_failed brand_kit_id=%s copied=%d error=%s", kit.pk, len(copies), type(exc).__name__,
        )
        if isinstance(exc, StoredFileCopyError):
            raise BrandKitCopyError(str(exc)) from exc
        raise
    logger.info("brand_kit.duplicated brand_kit_id=%s copy_id=%s files=%d", kit.pk, copy.pk, len(copies))
    return copy
