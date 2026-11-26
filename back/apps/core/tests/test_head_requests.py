"""
A HEAD runs the GET handler (Django's View.setup), so WorkspaceRolePermission
asks it for GET's action. Before, a HEAD fell to ``project.view``: a Lector
got a 200 on what GET refuses, and the work of the GET ran.

Checked on every routed view that maps its requests with ``workspace_actions``,
so a new view cannot bring the hole back.
"""
from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver

from apps.core.permissions import WorkspaceRolePermission


class _Request:
    """Only what required_action reads."""

    def __init__(self, method):
        self.method = method


def _routes(patterns, prefix=''):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _routes(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def _view(callback, method):
    """The view instance a request with ``method`` gets, with its viewset action set."""
    view = callback.cls(**getattr(callback, 'initkwargs', {}))
    actions = getattr(callback, 'actions', None)
    if actions is not None:
        view.action = actions.get(method.lower())
    return view


def scoped_get_routes():
    """(route, callback) of every view with WorkspaceRolePermission, workspace_actions and a GET."""
    for route, callback in _routes(get_resolver().url_patterns):
        cls = getattr(callback, 'cls', None)
        if cls is None or WorkspaceRolePermission not in getattr(cls, 'permission_classes', ()):
            continue
        if not getattr(cls, 'workspace_actions', None):
            continue
        actions = getattr(callback, 'actions', None)
        has_get = 'get' in actions if actions is not None else hasattr(cls, 'get')
        if has_get:
            yield route, callback


class HeadIsGetTests(SimpleTestCase):
    def test_every_scoped_view_asks_head_for_the_action_of_get(self):
        routes = list(scoped_get_routes())
        # The wallet's are there: the ones a Lector must not read.
        self.assertTrue(any('transactions' in route for route, _ in routes))
        for route, callback in routes:
            with self.subTest(route=route):
                head = WorkspaceRolePermission.required_action(_Request('HEAD'), _view(callback, 'HEAD'))
                get = WorkspaceRolePermission.required_action(_Request('GET'), _view(callback, 'GET'))
                self.assertEqual(head, get)
