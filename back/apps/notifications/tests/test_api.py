"""
The bell's endpoints (front contract ``notifications.*``): the request user's
own notifications in the active workspace, for every role. Another person's,
or one of another workspace, is a 404 and is never touched.
"""
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APITestCase

from apps.notifications import services
from apps.notifications.models import Notification
from apps.users.models import WorkspaceMember

from .helpers import ADMIN, EDITOR, PENDING, VIEWER, join, make_user, team

LIST, READ_ALL = reverse('notification-list'), reverse('notification-read-all')
START = datetime(2026, 11, 4, 15, 0, tzinfo=dt_timezone.utc)
NOW = START + timedelta(days=1)
KEYS = {'id', 'type', 'title', 'body', 'created_at', 'read_at', 'project_id', 'post_id'}


def read(notification):
    return reverse('notification-read', args=[notification.pk if isinstance(notification, Notification) else notification])


def header(workspace):
    return {'HTTP_X_WORKSPACE_ID': str(workspace.pk)}


def note(user, workspace, minutes=0, kind='project_ready', read_at=None, title='Listo', **fields):
    row = Notification.objects.create(user=user, workspace=workspace, kind=kind, title=title, body='Cuerpo', read_at=read_at, **fields)
    Notification.objects.filter(pk=row.pk).update(created_at=START + timedelta(minutes=minutes))
    row.refresh_from_db()
    return row


class BellHarness(APITestCase):
    def setUp(self):
        self.ana, self.edu, self.otto = make_user('ana'), make_user('edu'), make_user('otto')
        self.home = team(self.ana, 'Casa')
        self.other = team(self.ana, 'Otro')
        join(self.home, self.edu)
        self.client.force_authenticate(self.ana)
        self.ws = header(self.home)


class ListTests(BellHarness):
    def test_the_newest_first_with_the_contract_fields(self):
        old = note(self.ana, self.home, minutes=1, read_at=START + timedelta(minutes=5))
        new = note(self.ana, self.home, minutes=2, kind='project_failed')

        response = self.client.get(LIST, **self.ws)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data), {'unread_count', 'results'})
        self.assertEqual([row['id'] for row in response.data['results']], [str(new.pk), str(old.pk)])
        self.assertEqual(response.data['unread_count'], 1)
        first, second = response.json()['results']
        self.assertEqual(set(first), KEYS)
        self.assertEqual(
            first,
            {
                'id': str(new.pk), 'type': 'project_failed', 'title': 'Listo', 'body': 'Cuerpo',
                'created_at': '2026-11-04T15:02:00Z', 'read_at': None, 'project_id': None, 'post_id': None,
            },
        )
        self.assertEqual(second['read_at'], '2026-11-04T15:05:00Z')

    def test_only_mine_in_the_active_workspace(self):
        mine = note(self.ana, self.home)
        note(self.edu, self.home)
        note(self.ana, self.other)

        response = self.client.get(LIST, **self.ws)

        self.assertEqual([row['id'] for row in response.data['results']], [str(mine.pk)])
        self.assertEqual(response.data['unread_count'], 1)
        elsewhere = self.client.get(LIST, **header(self.other))
        self.assertEqual((len(elsewhere.data['results']), elsewhere.data['unread_count']), (1, 1))

    def test_at_most_twenty_and_every_unread_one_is_counted(self):
        for minute in range(23):
            note(self.ana, self.home, minutes=minute, title=f'N{minute}')

        response = self.client.get(LIST, **self.ws)

        self.assertEqual([row['title'] for row in response.data['results']], [f'N{minute}' for minute in range(22, 2, -1)])
        self.assertEqual(response.data['unread_count'], 23)

    def test_empty(self):
        self.assertEqual(self.client.get(LIST, **self.ws).json(), {'unread_count': 0, 'results': []})

    def test_the_link_to_the_project(self):
        from apps.videos.models import VideoProject

        project = VideoProject.objects.create(workspace=self.home, uploaded_by=self.ana, title='P', source_file='a/b.mp4')
        note(self.ana, self.home, project=project)

        self.assertEqual(self.client.get(LIST, **self.ws).json()['results'][0]['project_id'], str(project.pk))

    def test_the_list_takes_a_fixed_number_of_queries(self):
        for minute in range(5):
            note(self.ana, self.home, minutes=minute)

        with self.assertNumQueries(3):  # the workspace, the count and the page
            self.client.get(LIST, **self.ws)


class ReadTests(BellHarness):
    def test_marks_it_read_once(self):
        row = note(self.ana, self.home)

        with patch('django.utils.timezone.now', return_value=NOW):
            response = self.client.post(read(row), **self.ws)
        later = self.client.post(read(row), **self.ws)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), KEYS)
        self.assertEqual(response.json()['read_at'], '2026-11-05T15:00:00Z')
        self.assertEqual(later.json()['read_at'], '2026-11-05T15:00:00Z')
        self.assertEqual(self.client.get(LIST, **self.ws).data['unread_count'], 0)

    def test_of_another_person_or_workspace_is_a_404_and_stays_unread(self):
        theirs = note(self.edu, self.home)
        elsewhere = note(self.ana, self.other)

        for row in (theirs, elsewhere):
            with self.subTest(row=row.pk):
                self.assertEqual(self.client.post(read(row), **self.ws).status_code, 404)
                row.refresh_from_db()
                self.assertIsNone(row.read_at)

    def test_one_that_does_not_exist_is_a_404(self):
        self.assertEqual(self.client.post(read(uuid.uuid4()), **self.ws).status_code, 404)
        self.assertEqual(self.client.post(LIST + 'no-es-un-id/read/', **self.ws).status_code, 404)


class ReadAllTests(BellHarness):
    def test_marks_only_mine_in_this_workspace(self):
        already = START + timedelta(minutes=3)
        first, second = note(self.ana, self.home), note(self.ana, self.home, minutes=1)
        seen = note(self.ana, self.home, minutes=2, read_at=already)
        theirs, elsewhere = note(self.edu, self.home), note(self.ana, self.other)

        with patch('django.utils.timezone.now', return_value=NOW):
            response = self.client.post(READ_ALL, **self.ws)

        self.assertEqual((response.status_code, response.content), (204, b''))
        read_at = dict(Notification.objects.values_list('pk', 'read_at'))
        self.assertEqual(
            [read_at[row.pk] for row in (first, second, seen, theirs, elsewhere)], [NOW, NOW, already, None, None],
        )


class PermissionTests(BellHarness):
    def test_every_role_reads_and_marks_its_own(self):
        for role in (ADMIN, EDITOR, VIEWER):
            with self.subTest(role=role):
                user = make_user(f'rol_{role}')
                join(self.home, user, role)
                first, second = note(user, self.home), note(user, self.home, minutes=1)
                self.client.force_authenticate(user)

                self.assertEqual(self.client.get(LIST, **self.ws).data['unread_count'], 2)
                self.assertEqual(self.client.post(read(first), **self.ws).status_code, 200)
                self.assertEqual(self.client.post(READ_ALL, **self.ws).status_code, 204)
                second.refresh_from_db()
                self.assertIsNotNone(second.read_at)

    def test_without_a_session_it_is_a_401(self):
        row = note(self.ana, self.home)
        self.client.force_authenticate(None)

        for response in (self.client.get(LIST), self.client.post(read(row)), self.client.post(READ_ALL)):
            self.assertEqual(response.status_code, 401)

    def test_a_workspace_that_is_not_mine_is_a_404(self):
        for outsider, how in ((self.otto, None), (make_user('ivan'), PENDING)):
            if how:
                join(self.home, outsider, status=how)
            note(outsider, self.home)
            self.client.force_authenticate(outsider)
            with self.subTest(user=outsider.username):
                self.assertEqual(self.client.get(LIST, **self.ws).status_code, 404)
                self.assertEqual(self.client.post(READ_ALL, **self.ws).status_code, 404)
        self.assertFalse(Notification.objects.filter(read_at__isnull=False).exists())

    def test_who_left_the_workspace_no_longer_reads_what_they_got_there(self):
        row = note(self.edu, self.home)
        WorkspaceMember.objects.filter(workspace=self.home, user=self.edu).delete()
        self.client.force_authenticate(self.edu)

        self.assertEqual(self.client.get(LIST, **self.ws).status_code, 404)
        self.assertEqual(self.client.post(read(row), **self.ws).status_code, 404)
        # Their own workspace (no header) does not show it either.
        self.assertEqual(self.client.get(LIST).json(), {'unread_count': 0, 'results': []})

    def test_other_methods_are_not_allowed(self):
        row = note(self.ana, self.home)

        self.assertEqual(self.client.post(LIST, **self.ws).status_code, 405)
        self.assertEqual(self.client.get(read(row), **self.ws).status_code, 405)
        self.assertEqual(self.client.delete(read(row), **self.ws).status_code, 405)
        self.assertEqual(self.client.get(READ_ALL, **self.ws).status_code, 405)


class BoundTests(BellHarness):
    def write(self, title, users=None):
        return services._write(self.home, users or {self.ana.pk}, 'project_ready', title, 'Cuerpo')

    def test_only_the_newest_of_each_user_in_each_workspace_are_kept(self):
        kept = services.KEPT_PER_USER
        for minute in range(kept):
            note(self.ana, self.home, minutes=minute, title=f'N{minute}')
        theirs, elsewhere = note(self.edu, self.home, minutes=-5), note(self.ana, self.other, minutes=-5)

        self.write('Nueva')
        self.write('Otra más')

        titles = list(Notification.objects.filter(user=self.ana, workspace=self.home).order_by('created_at').values_list('title', flat=True))
        self.assertEqual(titles, [f'N{minute}' for minute in range(2, kept)] + ['Nueva', 'Otra más'])
        self.assertTrue(Notification.objects.filter(pk__in=[theirs.pk, elsewhere.pk]).count() == 2)

    def test_it_is_written_only_for_who_can_act_in_the_workspace(self):
        ivan = make_user('ivan')
        join(self.home, ivan, status=PENDING)

        written = self.write('Para todos', users={self.ana.pk, self.edu.pk, self.otto.pk, ivan.pk})

        self.assertEqual(written, 2)
        self.assertEqual(set(Notification.objects.values_list('user__username', flat=True)), {'ana', 'edu'})

    def test_long_texts_fit(self):
        self.write('t' * 400)

        self.assertEqual(len(Notification.objects.get().title), 300)

    def test_amounts_are_written_like_the_app_does(self):
        cases = {'1250.50': '1.250,50', '20': '20', '4.5000000000': '4,50', '0.126': '0,13', '1000000': '1.000.000', '20.00': '20'}
        for amount, text in cases.items():
            self.assertEqual(services.format_tokens(amount), text)


class SchemaTests(APITestCase):
    def test_the_schema_documents_the_three_endpoints(self):
        paths = SchemaGenerator().get_schema(request=None, public=True)['paths']

        self.assertEqual(set(paths['/api/v1/notifications/']['get']['responses']), {'200', '401', '404'})
        self.assertEqual(set(paths['/api/v1/notifications/{notification_id}/read/']['post']['responses']), {'200', '401', '404'})
        self.assertEqual(set(paths['/api/v1/notifications/read-all/']['post']['responses']), {'204', '401', '404'})
