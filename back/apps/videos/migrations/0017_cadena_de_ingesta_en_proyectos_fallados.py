"""
A chain of ingestion messages for every project that is failed at deploy time
and has none (tasks/ingestion.py, INGESTION_CHAIN_KEY).

A message without a token is the chain of a project with none stored, so a
tokenless copy still in flight when this deploys (sent by the code before
chains, and only delayed) could reopen a failed project, whose reservation
was refunded, and run it for free. With a token no message has stored on it,
only a re-dispatch or the moderation override, which mint their own, reopen
it. Projects that are not failed keep running their tokenless chain.

Never raises on real data: a metadata that is not a dict is left as it is,
and each row is written with a compare-and-set on the metadata it was read
with (a row that changed meanwhile is left alone). Reversing does nothing:
the tokens are harmless without the code that reads them.
"""

import uuid

from django.db import migrations

BATCH_SIZE = 500
CHAIN_KEY = 'ingestion_chain'


def store_chains(apps, schema_editor):
    VideoProject = apps.get_model('videos', 'VideoProject')
    failed = VideoProject.objects.filter(pipeline_stage='failed').order_by('pk').values_list('pk', 'metadata')
    for pk, metadata in failed.iterator(chunk_size=BATCH_SIZE):
        if not isinstance(metadata, dict) or metadata.get(CHAIN_KEY):
            continue
        VideoProject.objects.filter(pk=pk, pipeline_stage='failed', metadata=metadata).update(
            metadata={**metadata, CHAIN_KEY: uuid.uuid4().hex},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('videos', '0016_layouts_podcast_trending_y_opciones'),
    ]

    operations = [
        migrations.RunPython(store_chains, migrations.RunPython.noop),
    ]
