"""
payments 0015-0016: the transactions that already exist get their project and
category from what the database keeps (the project's ``*reservation_id`` keys,
the replaced ones, the description), never from another workspace, and the
migration does not stop on a metadata it cannot read.
"""
import importlib
import uuid
from decimal import Decimal

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.core.testing import clear_local_caches

BEFORE = [('payments', '0014_pro_plan_ten_members')]
AFTER = [('payments', '0016_backfill_transaction_project')]
backfill_migration = importlib.import_module('apps.payments.migrations.0016_backfill_transaction_project')


class TransactionBackfillMigrationTests(TransactionTestCase):

    def tearDown(self):
        call_command('migrate', verbosity=0)
        clear_local_caches()

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        # The projects are read at the videos app's latest state, as on a deploy.
        target = [*target, *executor.loader.graph.leaf_nodes('videos')]
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def setUp(self):
        apps = self.migrate(BEFORE)
        self.User = apps.get_model('users', 'User')
        self.Workspace = apps.get_model('users', 'Workspace')
        self.Wallet = apps.get_model('payments', 'Wallet')
        self.Transaction = apps.get_model('payments', 'Transaction')
        self.VideoProject = apps.get_model('videos', 'VideoProject')
        owner = self.User.objects.create(username='dueno', email='dueno@test.com', password='!')
        self.home = self.Workspace.objects.create(name='Casa', owner=owner)
        self.other = self.Workspace.objects.create(name='Otro', owner=owner)
        self.wallet = self.Wallet.objects.create(workspace=self.home)
        self.other_wallet = self.Wallet.objects.create(workspace=self.other)

    def txn(self, description, kind='SPEND', status='COMPLETED', wallet=None):
        return self.Transaction.objects.create(
            wallet=wallet or self.wallet, amount=Decimal('1'), transaction_type=kind, status=status, description=description,
        )

    def project(self, metadata, workspace=None, title='Proyecto'):
        return self.VideoProject.objects.create(workspace=workspace or self.home, title=title, metadata=metadata)

    def after(self):
        apps = self.migrate(AFTER)
        Transaction = apps.get_model('payments', 'Transaction')
        return {str(row.pk): (row.project_id, row.category) for row in Transaction.objects.all()}

    def test_each_reservation_named_in_metadata_is_linked_with_its_category(self):
        creation = self.txn('Procesamiento: Uno')
        approval = self.txn('Render Final (Paper Edit): Uno', status='RESERVED')
        auto = self.txn('Render automático: Uno', status='FAILED')
        clip = self.txn('Render de clip: Gancho')
        replaced_creation = self.txn('Procesamiento: Uno', status='FAILED')
        replaced_clip = self.txn('Reintento de clip: Gancho')
        project = self.project({
            'reservation_id': str(creation.pk),
            'render_reservation_id': str(approval.pk),
            'auto_render_reservation_id': str(auto.pk),
            'clip_render_reservation_id': str(clip.pk),
            'replaced_reservation_ids': [str(replaced_creation.pk), str(replaced_clip.pk)],
        })

        links = self.after()

        self.assertEqual(links[str(creation.pk)], (project.pk, 'project'))
        self.assertEqual(links[str(approval.pk)], (project.pk, 'render'))
        self.assertEqual(links[str(auto.pk)], (project.pk, 'render'))
        self.assertEqual(links[str(clip.pk)], (project.pk, 'render'))
        self.assertEqual(links[str(replaced_creation.pk)], (project.pk, 'project'))
        self.assertEqual(links[str(replaced_clip.pk)], (project.pk, 'render'))

    def test_what_no_metadata_names_keeps_no_project_but_gets_its_category(self):
        # An auto-render whose keys the approval dropped, a purchase and something unknown.
        dropped = self.txn('Render automático: Uno')
        purchase = self.txn('Compra de 1000 tokens', kind='DEPOSIT')
        unknown = self.txn('Ajuste manual')

        links = self.after()

        self.assertEqual(links[str(dropped.pk)], (None, 'render'))
        self.assertEqual(links[str(purchase.pk)], (None, 'purchase'))
        self.assertEqual(links[str(unknown.pk)], (None, 'other'))

    def test_never_to_a_project_of_another_workspace_and_the_oldest_project_wins(self):
        foreign = self.txn('Procesamiento: Ajeno', wallet=self.other_wallet)
        shared = self.txn('Procesamiento: Uno')
        self.project({'reservation_id': str(foreign.pk)})  # a project of home naming other's transaction
        first = self.project({'reservation_id': str(shared.pk)}, title='Primero')
        self.project({'reservation_id': str(shared.pk)}, title='Copia')

        links = self.after()

        self.assertEqual(links[str(foreign.pk)], (None, 'project'))
        self.assertEqual(links[str(shared.pk)], (first.pk, 'project'))

    def test_a_metadata_it_cannot_read_is_skipped(self):
        good = self.txn('Procesamiento: Bien')
        for metadata in (
            ['reservation_id'], 'texto', 7, True, {},
            {'reservation_id': 'no-es-un-uuid', 'render_reservation_id': 12, 'replaced_reservation_ids': 'x'},
            {'reservation_id': str(uuid.uuid4()), 'replaced_reservation_ids': [None, 3, 'nada', {'a': 1}]},
        ):
            self.project(metadata)
        project = self.project({'reservation_id': str(good.pk)})

        links = self.after()

        self.assertEqual(links[str(good.pk)], (project.pk, 'project'))

    def test_running_it_again_changes_nothing(self):
        txn = self.txn('Procesamiento: Uno')
        project = self.project({'reservation_id': str(txn.pk)})
        apps = self.migrate(AFTER)
        Transaction = apps.get_model('payments', 'Transaction')

        again = backfill_migration.backfill(Transaction, apps.get_model('videos', 'VideoProject'))

        self.assertEqual(again, 0)
        self.assertEqual(Transaction.objects.get(pk=txn.pk).project_id, project.pk)
