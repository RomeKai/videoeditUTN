import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Project } from '@/api/contracts/projects';
import { seedDb } from '@/api/mocks/seed';
import { serializeProject } from '@/api/mocks/serialize';
import { headerStatusText } from '@/features/proyecto/model';
import { formatEta } from '@/lib/format';
import { describeProjectStatus } from '@/lib/project-status';
import { SummaryCard, type SummaryProps } from './NewProjectSummary';
import { ProjectStatusLine } from './ProjectStatusLine';

const db = seedDb({ scenario: 'todos-los-estados', projects: 'all-states' }, new Date('2026-11-24T18:00:00Z').getTime());
const byTitle = (title: string): Project => {
  const found = db.projects.find((project) => project.title.startsWith(title));
  if (!found) throw new Error(`No hay proyecto «${title}»`);
  return serializeProject(db, found, { full: true });
};

const summary = (etaSeconds: number): SummaryProps => ({
  rows: [{ key: 'video', label: 'Video', value: 'corto.mp4' }],
  watermarkPlan: undefined,
  cost: { kind: 'exact', total: 20 },
  detail: [],
  available: 4860,
  after: { label: 'Saldo después', value: 4840 },
  missing: null,
  required: 20,
  etaSeconds,
  renderNote: null,
  note: 'Reservamos los tokens al crear.',
  cta: { phase: 'idle', upload: null, onCreate: vi.fn(), onCancel: vi.fn(), blocked: null, canBuy: true, hint: '', error: null },
});

/** What the backend may send as `eta_seconds`, with how every screen writes it. */
const CASES: Array<[number, string]> = [
  [1, '≈ 1 s'],
  [10, '≈ 10 s'],
  [29, '≈ 29 s'],
  [59, '≈ 59 s'],
  [60, '≈ 1 min'],
  [89, '≈ 1 min'],
  // From a minute on it rounds to the nearest minute: a minute and a half is two.
  [90, '≈ 2 min'],
  [542, '≈ 9 min'],
  [3600, '≈ 1 h'],
  [3900, '≈ 1 h 5 min'],
];

describe('el tiempo estimado se lee igual en todas las pantallas', () => {
  it.each(CASES)('%d s es «%s» en Nuevo proyecto, en la cabecera del proyecto (en proceso y en cola) y en la lista', (seconds, text) => {
    expect(formatEta(seconds)).toBe(text);

    // C1 · Nuevo proyecto.
    const created = render(<SummaryCard {...summary(seconds)} />);
    expect(screen.getByText(/Listo para revisar en/)).toHaveTextContent(`Listo para revisar en ${text}`);
    created.unmount();

    for (const project of [byTitle('Clase 3'), byTitle('En cola: recién subido')]) {
      const estimated = { ...project, eta_seconds: seconds };
      const view = describeProjectStatus(estimated);
      // D1 · the project header.
      expect(headerStatusText(estimated, view)).toBe(`${view.text.replace(/\.$/, '')} · faltan ${text} para revisar`);
      // B2 · the row of the list.
      const row = render(<ProjectStatusLine project={estimated} view={view} />);
      expect(screen.getByText(text)).toHaveAttribute('title', `Faltan ${text} para revisar`);
      expect(row.container).toHaveTextContent(`${view.shortText} · ${text}`);
      row.unmount();
    }
  });

  it('sin estimado ninguna lo inventa', () => {
    for (const eta of [null, undefined, 0]) {
      const project = { ...byTitle('En cola: recién subido'), eta_seconds: eta };
      const view = describeProjectStatus(project);
      expect(headerStatusText(project, view)).toBe(view.text);
      const row = render(<ProjectStatusLine project={project} view={view} />);
      expect(row.container).not.toHaveTextContent('≈');
      row.unmount();
    }
  });
});
