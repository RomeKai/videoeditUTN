'use client';

import { useEffect, useState } from 'react';
import { API_MODE } from '@/api/config';
import { hybridBlockReason, SCENARIOS, type Scenario } from '@/api/mocks/scenarios';
import { applyScenario, getScenarioId } from '@/api/mocks/state';
import styles from './MockPanel.module.css';

const GROUPS: Scenario['group'][] = ['Datos', 'Plan', 'Rol', 'Sesión', 'Sistema'];

/** Where each scenario starts, so the switch lands on the screen it is about. */
const START: Record<string, string> = {
  'usuario-nuevo': '/proyectos',
  'sin-sesion': '/login',
  'email-sin-verificar': '/login',
};

export function switchScenario(id: string): void {
  applyScenario(id);
  const start = START[id];
  // Hybrid: the session is the real one, so the scenario never changes who is signed in or where they are.
  if (start && API_MODE !== 'hybrid') window.location.assign(start);
  else window.location.reload();
}

/**
 * List of mock scenarios; choosing one seeds its data, signs in its user and reloads.
 * In hybrid mode the scenarios about the mocked session or its data are disabled, with the reason.
 */
export function ScenarioPicker({ hybrid = API_MODE === 'hybrid' }: { hybrid?: boolean }) {
  const [current, setCurrent] = useState<string | null>(null);
  useEffect(() => setCurrent(getScenarioId()), []);
  return (
    <div className={styles.list} role="group" aria-label="Escenarios de datos de prueba">
      {GROUPS.map((group) => (
        <div key={group} style={{ display: 'contents' }}>
          <p className={`t-over ${styles.group}`}>{group}</p>
          {SCENARIOS.filter((scenario) => scenario.group === group).map((scenario) => {
            const blocked = hybrid ? hybridBlockReason(scenario) : null;
            return (
              <button
                key={scenario.id}
                type="button"
                className={styles.item}
                aria-pressed={scenario.id === current}
                disabled={blocked !== null}
                onClick={() => switchScenario(scenario.id)}
              >
                <span className={styles.itemName}>{scenario.label}</span>
                <span className={styles.itemDesc}>{scenario.description}</span>
                {blocked ? <span className={styles.itemWhy}>{blocked}</span> : null}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}
