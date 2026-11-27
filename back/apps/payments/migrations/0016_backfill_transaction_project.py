import logging
import uuid

from django.db import migrations

# Fills Transaction.project and Transaction.category (0015) on the rows that
# already exist, with what the database keeps:
#
# - A project's metadata names its reservations (``reservation_id``,
#   ``render_reservation_id``, ``auto_render_reservation_id``,
#   ``clip_render_reservation_id``, and the ones a later reservation replaced in
#   ``replaced_reservation_ids``). Each one is linked to that project, only when
#   the transaction is of the project's workspace and nothing linked it yet.
# - The category comes from that key, else from the description the code
#   writes (``Procesamiento: …`` is the project, ``Render …`` and
#   ``Reintento de clip: …`` a render). A DEPOSIT is a purchase.
#
# What cannot be told stays as it is: no project and category ``other`` (an
# auto-render whose keys the approval dropped keeps its ``render`` category
# but no project). It never raises on what it reads: a metadata that is not an
# object, or an id that is not a UUID, is skipped. Reversing does nothing:
# 0015 drops the columns.

RESERVATION_KEYS = {
    'reservation_id': 'project',
    'render_reservation_id': 'render',
    'auto_render_reservation_id': 'render',
    'clip_render_reservation_id': 'render',
}
REPLACED_KEY = 'replaced_reservation_ids'
DESCRIPTION_CATEGORIES = (
    ('Procesamiento:', 'project'),
    ('Render ', 'render'),
    ('Reintento de clip:', 'render'),
)

logger = logging.getLogger(__name__)


def _uuid(value):
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def linked_reservations(metadata):
    """{transaction id: category or None} that a project's metadata names."""
    if not isinstance(metadata, dict):
        return {}
    links = {}
    for key, category in RESERVATION_KEYS.items():
        txn_id = _uuid(metadata.get(key))
        if txn_id is not None:
            links[txn_id] = category
    replaced = metadata.get(REPLACED_KEY)
    if isinstance(replaced, list):
        for value in replaced:
            txn_id = _uuid(value)
            if txn_id is not None:
                # A replaced creation or a replaced clip render: its description says which.
                links.setdefault(txn_id, None)
    return links


def backfill(Transaction, VideoProject, using='default'):
    """Safe to run again: it only fills what is still empty. Returns how many rows it linked."""
    Transaction.objects.using(using).filter(transaction_type='DEPOSIT', category='other').update(category='purchase')
    for prefix, category in DESCRIPTION_CATEGORIES:
        Transaction.objects.using(using).filter(
            transaction_type='SPEND', category='other', description__startswith=prefix,
        ).update(category=category)

    linked = 0
    projects = VideoProject.objects.using(using).order_by('created_at', 'pk').values_list('pk', 'workspace_id', 'metadata')
    for project_id, workspace_id, metadata in projects.iterator(chunk_size=500):
        for txn_id, category in linked_reservations(metadata).items():
            fields = {'project_id': project_id}
            if category is not None:
                fields['category'] = category
            # The oldest project that names it wins; another workspace's never does.
            linked += Transaction.objects.using(using).filter(
                pk=txn_id, wallet__workspace_id=workspace_id, project__isnull=True,
            ).update(**fields)
    return linked


def forwards(apps, schema_editor):
    using = schema_editor.connection.alias
    linked = backfill(apps.get_model('payments', 'Transaction'), apps.get_model('videos', 'VideoProject'), using=using)
    logger.info('payments.transactions_linked count=%d', linked)


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0015_transaction_project_category'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
