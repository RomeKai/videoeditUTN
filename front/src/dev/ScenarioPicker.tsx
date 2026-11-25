'use client';

import { useEffect, useState } from 'react';
import { SCENARIOS, type Scenario } from '@/api/mocks/scenarios';
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
  if (start) window.location.assign(start);
  else window.location.reload();
}

/** List of mock scenarios; choosing one seeds its data, signs in its user and reloads. */
export function ScenarioPicker() {
  const [current, setCurrent] = useState<string | null>(null);
  useEffect(() => setCurrent(getScenarioId()), []);
  return (
    <div className={styles.list} role="group" aria-label="Escenarios de datos de prueba">
      {GROUPS.map((group) => (
        <div key={group} style={{ display: 'contents' }}>
          <p className={`t-over ${styles.group}`}>{group}</p>
          {SCENARIOS.filter((scenario) => scenario.group === group).map((scenario) => (
            <button
              key={scenario.id}
              type="button"
              className={styles.item}
              aria-pressed={scenario.id === current}
              onClick={() => switchScenario(scenario.id)}
            >
              <span className={styles.itemName}>{scenario.label}</span>
              <span className={styles.itemDesc}>{scenario.description}</span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
