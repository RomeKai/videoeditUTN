import { describe, expect, it } from 'vitest';
import { Role } from '@/api/contracts/common';
import { MATRIX } from './permissions';
import backend from './role-matrix.json';

/**
 * `role-matrix.json` is the backend's `ROLE_MATRIX` (back/apps/core/permissions.py),
 * written by `python manage.py export_role_matrix`. A backend test
 * (back/apps/core/tests/test_role_matrix_fixture.py) fails when the file falls
 * behind, and this one fails when `MATRIX` does not say the same: a difference
 * means the UI enables a button the API answers with 403, or disables one the
 * API would accept.
 */
type Table = Record<string, readonly string[]>;

function differences(front: Table, back: Table, roles: readonly string[]): string[] {
  const out: string[] = [];
  const actions = [...new Set([...Object.keys(front), ...Object.keys(back)])].sort();
  for (const action of actions) {
    const inFront = front[action];
    const inBack = back[action];
    if (!inBack) {
      out.push(`acción «${action}»: está en MATRIX (front) y no en ROLE_MATRIX (back)`);
    } else if (!inFront) {
      out.push(`acción «${action}»: está en ROLE_MATRIX (back) y no en MATRIX (front)`);
    } else {
      for (const role of roles) {
        const ui = inFront.includes(role);
        const api = inBack.includes(role);
        if (ui === api) continue;
        out.push(
          ui
            ? `rol «${role}», acción «${action}»: la UI la habilita y la API responde 403`
            : `rol «${role}», acción «${action}»: la API la permite y la UI la deshabilita`,
        );
      }
    }
  }
  return out;
}

const HOW_TO_FIX =
  'Cambiá MATRIX (front/src/lib/permissions.ts) y ROLE_MATRIX (back/apps/core/permissions.py) juntas, ' +
  'y después corré `python manage.py export_role_matrix` desde back/ para actualizar role-matrix.json.';

describe('tabla de roles: frontend y backend', () => {
  it('los roles del contrato son los del backend', () => {
    expect([...Role.options].sort()).toEqual([...backend.roles].sort());
  });

  it('MATRIX dice lo mismo que ROLE_MATRIX, rol por rol y acción por acción', () => {
    const found = differences(MATRIX, backend.matrix, backend.roles);
    expect(found, `La tabla de roles no coincide con la del backend:\n  ${found.join('\n  ')}\n${HOW_TO_FIX}\n`).toEqual([]);
  });

  it('una diferencia se informa con el rol y la acción', () => {
    const roles = ['owner', 'admin', 'editor', 'viewer'];
    expect(
      differences(
        { 'post.publish': ['owner', 'admin', 'editor'], 'clip.edit': ['owner'], 'solo.front': ['owner'] },
        { 'post.publish': ['owner', 'admin'], 'clip.edit': ['owner', 'admin'], 'solo.back': ['owner'] },
        roles,
      ),
    ).toEqual([
      'rol «admin», acción «clip.edit»: la API la permite y la UI la deshabilita',
      'rol «editor», acción «post.publish»: la UI la habilita y la API responde 403',
      'acción «solo.back»: está en ROLE_MATRIX (back) y no en MATRIX (front)',
      'acción «solo.front»: está en MATRIX (front) y no en ROLE_MATRIX (back)',
    ]);
  });
});
