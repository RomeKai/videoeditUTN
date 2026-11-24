'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { findScenario } from '@/api/mocks/scenarios';
import { getScenarioId } from '@/api/mocks/state';
import { Button, IconButton } from '@/ui/Button';
import { Icon } from '@/ui/Icon';
import styles from './MockPanel.module.css';
import { ScenarioPicker, switchScenario } from './ScenarioPicker';

/**
 * Small floating panel shown only in mock mode: which scenario is loaded, a
 * switcher, a reset of the mock data and a link to the component kit.
 */
export function MockPanel() {
  const [open, setOpen] = useState(false);
  const [scenario, setScenario] = useState('');
  useEffect(() => setScenario(findScenario(getScenarioId()).label), []);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  return (
    <>
      <button type="button" className={styles.fab} aria-expanded={open} aria-controls="mock-panel" onClick={() => setOpen((value) => !value)}>
        <Icon name="beaker" size={14} />
        Mocks{scenario ? `: ${scenario}` : ''}
      </button>
      {open ? (
        <section className={styles.panel} id="mock-panel" aria-label="Datos de prueba">
          <div className={styles.head}>
            <div style={{ flex: 1 }}>
              <h2 className="t-h3">Datos de prueba</h2>
              <p className="t-small muted">La API está simulada en el navegador.</p>
            </div>
            <IconButton icon="close" label="Cerrar" onClick={() => setOpen(false)} />
          </div>
          <ScenarioPicker />
          <div className={styles.foot}>
            <Button size="sm" variant="ghost" iconLeft="retry" onClick={() => switchScenario(getScenarioId())}>
              Reiniciar datos
            </Button>
            <Link className="lnk t-small" href="/dev/kit" onClick={() => setOpen(false)}>
              Ver el kit
            </Link>
          </div>
        </section>
      ) : null}
    </>
  );
}
