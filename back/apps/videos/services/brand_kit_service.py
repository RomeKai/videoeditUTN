"""Brand kit rules that touch more than one row or the stored files."""
import logging
import os
import uuid

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


def _delete_copies(copies, source_names=()) -> None:
    """
    Best effort: a copy that cannot be deleted is logged and left (no row points to it).
    A name in ``source_names`` (a file of the original kit) is never deleted.
    """
    for storage, name in copies:
        if name in source_names:
            logger.warning("brand_kit.copy_cleanup_skipped_source")
            continue
        try:
            storage.delete(name)
        except Exception as exc:
            logger.warning("brand_kit.copy_cleanup_failed error=%s", type(exc).__name__)


def _file_fields():
    return [f for f in BrandKit._meta.concrete_fields if isinstance(f, models.FileField)]


def stored_files(kit: BrandKit) -> list:
    """The files of the kit as ``(storage, name)``."""
    return [(value.storage, value.name) for value in (getattr(kit, f.name) for f in _file_fields()) if value]


def _is_referenced(name: str) -> bool:
    query = models.Q()
    for field in _file_fields():
        query |= models.Q(**{field.name: name})
    return BrandKit.objects.filter(query).exists()


def delete_files_on_commit(kit_id, files) -> None:
    """
    Deletes ``files`` (``(storage, name)``) once the current transaction
    commits: the files a deleted kit had, or the ones an update replaced.
    A name that a kit still uses is kept: copies own their files, but old rows
    and seeds may share one. Best effort: a storage error is logged and the
    file is left; it never fails the request.
    """
    files = list(files)
    if not files:
        return

    def _delete():
        deleted = 0
        for storage, name in files:
            try:
                if _is_referenced(name):
                    continue
                storage.delete(name)
                deleted += 1
            except Exception as exc:
                logger.warning("brand_kit.file_cleanup_failed brand_kit_id=%s error=%s", kit_id, type(exc).__name__)
        logger.info("brand_kit.files_deleted brand_kit_id=%s files=%d", kit_id, deleted)

    transaction.on_commit(_delete)


def copy_name(kit: BrandKit) -> str:
    """
    «Copia de {name}», or «Copia 2 de {name}», «Copia 3 de {name}»… when the
    workspace already has that one. Cut to the length of the field: the number
    is in the prefix, so two copies of a long name still differ. Call it under
    ``lock_workspace_default``, so two duplicates at once get different names.
    """
    max_length = BrandKit._meta.get_field('name').max_length
    taken = set(BrandKit.objects.filter(workspace_id=kit.workspace_id).values_list('name', flat=True))
    number = 1
    while True:
        prefix = COPY_NAME_PREFIX if number == 1 else f"Copia {number} de "
        name = f"{prefix}{kit.name}"[:max_length]
        if name not in taken:
            return name
        number += 1


def _copy_file_name(field, source_name: str, copy_id) -> str:
    """
    Where the copy of a file goes: the folder of the new kit (``upload_to``)
    with the name the file has, so the user sees the same «logo.png». The
    folder is new, so the storage adds no suffix and the name is never the
    source's.
    """
    return field.generate_filename(BrandKit(pk=copy_id), os.path.basename(source_name))


def duplicate_brand_kit(kit: BrandKit, user=None) -> BrandKit:
    """
    A copy of ``kit`` in its workspace, named by ``copy_name``, that is never
    the default. Every file (fonts, logo, intro, outro) is copied to the
    folder of the new kit in its storage, with its name, so each kit owns its
    files: deleting or replacing the files of one never touches the other's.

    All or nothing: the files are copied first and the row is written last.
    If a copy or the insert fails, the files already copied are deleted and
    ``BrandKitCopyError`` is raised; no row is left. The files of ``kit`` are
    never deleted: a copy that would get the name of its original is an error
    (``copy_stored_file``), and the cleanup skips the names ``kit`` uses.
    """
    values, copies = {}, []
    copy_id = uuid.uuid4()
    source_names = {name for _, name in stored_files(kit)}
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
                new_name = storage.get_available_name(
                    _copy_file_name(field, value.name, copy_id), max_length=field.max_length,
                )
                copy_stored_file(storage, value.name, new_name)
                copies.append((storage, new_name))
                value = new_name
            values[field.name] = value
        with transaction.atomic():
            lock_workspace_default(kit.workspace_id)
            copy = BrandKit.objects.create(
                pk=copy_id,
                workspace_id=kit.workspace_id,
                user=user,
                name=copy_name(kit),
                is_default=False,
                **values,
            )
    except Exception as exc:
        _delete_copies(copies, source_names)
        logger.warning(
            "brand_kit.duplicate_failed brand_kit_id=%s copied=%d error=%s", kit.pk, len(copies), type(exc).__name__,
        )
        if isinstance(exc, StoredFileCopyError):
            raise BrandKitCopyError(str(exc)) from exc
        raise
    logger.info("brand_kit.duplicated brand_kit_id=%s copy_id=%s files=%d", kit.pk, copy.pk, len(copies))
    return copy
