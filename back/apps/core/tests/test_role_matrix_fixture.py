"""
front/src/lib/role-matrix.json is ROLE_MATRIX as the frontend test reads it
(front/src/lib/permissions.test.ts). If it falls behind, the UI can enable a
button the API answers with 403, or hide one the API would accept.
"""
import json

import pytest

from apps.core.permissions import (
    OWNER,
    ROLE_MATRIX,
    ROLE_MATRIX_FIXTURE,
    ROLE_ORDER,
    role_matrix_export,
    role_matrix_json,
)

REGENERATE = "Run `python manage.py export_role_matrix` from back/ and commit front/src/lib/role-matrix.json."


def matrix_differences(expected, actual, roles):
    """One sentence per role and action the two tables disagree on."""
    differences = []
    for action in sorted(set(expected) | set(actual)):
        if action not in actual:
            differences.append(f"action '{action}' is in ROLE_MATRIX but not in role-matrix.json")
        elif action not in expected:
            differences.append(f"action '{action}' is in role-matrix.json but not in ROLE_MATRIX")
        else:
            for role in roles:
                in_backend, in_fixture = role in expected[action], role in actual[action]
                if in_backend != in_fixture:
                    differences.append(
                        f"role '{role}', action '{action}': ROLE_MATRIX "
                        f"{'allows' if in_backend else 'denies'} it, role-matrix.json "
                        f"{'allows' if in_fixture else 'denies'} it"
                    )
    return differences


def test_matrix_differences_names_role_and_action():
    differences = matrix_differences(
        {'post.publish': ['owner', 'admin', 'editor'], 'project.view': ['owner']},
        {'post.publish': ['owner', 'admin'], 'wallet.view': ['owner']},
        ['owner', 'admin', 'editor', 'viewer'],
    )
    assert differences == [
        "role 'editor', action 'post.publish': ROLE_MATRIX allows it, role-matrix.json denies it",
        "action 'project.view' is in ROLE_MATRIX but not in role-matrix.json",
        "action 'wallet.view' is in role-matrix.json but not in ROLE_MATRIX",
    ]


def test_export_covers_every_role_of_the_matrix():
    used = set().union(*ROLE_MATRIX.values())
    assert used <= set(ROLE_ORDER), f"ROLE_ORDER is missing {sorted(used - set(ROLE_ORDER))}"


def test_export_covers_every_role_a_member_can_have():
    # A role added to WorkspaceMember.Role but not to ROLE_ORDER would be exported nowhere,
    # and the frontend's Role enum would reject it in /me at runtime.
    from apps.users.models import WorkspaceMember

    assert set(ROLE_ORDER) == {OWNER, *WorkspaceMember.Role.values}


def test_fixture_is_current():
    if not ROLE_MATRIX_FIXTURE.parent.exists():
        pytest.skip("front/ is not in this checkout (image built from back/ alone)")
    assert ROLE_MATRIX_FIXTURE.exists(), f"{ROLE_MATRIX_FIXTURE} does not exist. {REGENERATE}"

    text = ROLE_MATRIX_FIXTURE.read_text(encoding='utf-8')
    expected = role_matrix_export()
    fixture = json.loads(text)
    differences = matrix_differences(expected['matrix'], fixture.get('matrix', {}), expected['roles'])
    assert not differences, "role-matrix.json is out of date:\n  " + "\n  ".join(differences) + f"\n{REGENERATE}"
    assert text == role_matrix_json(), f"role-matrix.json has the same permissions but not the same text. {REGENERATE}"
