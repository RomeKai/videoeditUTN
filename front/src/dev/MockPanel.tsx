'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { API_MODE } from '@/api/config';
import { findScenario } from '@/api/mocks/scenarios';
import { getScenarioId } from '@/api/mocks/state';
import { Button, IconButton } from '@/ui/Button';
import { domainRouting } from '@/api/routingTable';
import { Icon } from '@/ui/Icon';
import { ApiRoutingPanel } from './ApiRoutingPanel';
import styles from './MockPanel.module.css';
import { ScenarioPicker, switchScenario } from './ScenarioPicker';

/**
 * Small floating panel shown only while the mocks run (modes `mock` and `hybrid`): which scenario is
 * loaded, a switcher, a reset of the mock data and a link to the component kit. In hybrid mode it also
 * says which domains the real backend answers, with a switch per domain.
 */
export function MockPanel({ hybrid = API_MODE === 'hybrid' }: { hybrid?: boolean }) {
  const [open, setOpen] = useState(false);
  const [scenario, setScenario] = useState('');
  const [origin, setOrigin] = useState('');
  useEffect(() => {
    setScenario(findScenario(getScenarioId()).label);
    if (!hybrid) return;
    const rows = domainRouting();
    const real = rows.filter((row) => row.route !== 'mock').length;
    setOrigin(`${real} de ${rows.length} dominios con backend real`);
  }, [hybrid]);

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
      {/* Collapsed to its icon and kept above the height of <ActionBar>, so it never covers the screen's own controls. */}
      <button type="button" className={styles.fab} aria-expanded={open} aria-controls="mock-panel" onClick={() => setOpen((value) => !value)}>
        <Icon name="beaker" size={14} />
        <span className={styles.fabLabel}>{hybrid ? `Híbrido${origin ? `: ${origin}` : ''}` : `Mocks${scenario ? `: ${scenario}` : ''}`}</span>
      </button>
      {open ? (
        <section className={styles.panel} id="mock-panel" aria-label="Datos de prueba">
          <div className={styles.head}>
            <div style={{ flex: 1 }}>
              <h2 className="t-h3">Datos de prueba</h2>
              <p className="t-small muted">
                {hybrid ? 'Modo híbrido: una parte de la API es el backend real y el resto está simulado en el navegador.' : 'La API está simulada en el navegador.'}
              </p>
            </div>
            <IconButton icon="close" label="Cerrar" onClick={() => setOpen(false)} />
          </div>
          <div className={styles.body}>
            {hybrid ? <ApiRoutingPanel /> : null}
            <ScenarioPicker hybrid={hybrid} />
          </div>
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
