import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { LayoutDiagram } from './LayoutDiagram';
import { FilePicker, type FileState } from './NewProjectOrigin';
import { SummaryCard, type SummaryProps } from './NewProjectSummary';
import { renderScreen } from '@/testing';

const MB = 1024 * 1024;

function summaryProps(patch: Partial<SummaryProps['cta']> = {}): SummaryProps {
  return {
    rows: [{ key: 'video', label: 'Video', value: 'podcast.mp4' }],
    watermarkPlan: undefined,
    cost: { kind: 'exact', total: 1944 },
    detail: [],
    available: 4860,
    after: { label: 'Saldo después', value: 2916 },
    missing: null,
    required: 1944,
    etaSeconds: 720,
    renderNote: null,
    note: 'Reservamos los tokens al crear.',
    cta: { phase: 'idle', upload: null, onCreate: vi.fn(), onCancel: vi.fn(), blocked: null, canBuy: true, hint: 'No se renderiza nada hasta que apruebes los clips.', error: null, ...patch },
  };
}

describe('C1 · subida', () => {
  it('muestra porcentaje, velocidad y Cancelar mientras sube', async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(<SummaryCard {...summaryProps({ phase: 'uploading', onCancel, upload: { loaded: 1344 * MB, total: 2100 * MB, speed: 18 * MB } })} />);
    const status = screen.getByRole('status', { name: 'Subiendo el video' });
    expect(within(status).getByText('64 %')).toBeInTheDocument();
    expect(within(status).getByRole('progressbar', { name: 'Progreso de la subida' })).toHaveAttribute('aria-valuenow', '64');
    expect(within(status).getByText('1,3 GB de 2,1 GB')).toBeInTheDocument();
    expect(within(status).getByText('18 MB/s · quedan 42 s')).toBeInTheDocument();
    expect(screen.getByText('No cierres esta pestaña hasta que termine. Después se abre el proyecto.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Crear proyecto' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it('con el video ya subido no ofrece Cancelar: el servidor ya está creando el proyecto', () => {
    render(<SummaryCard {...summaryProps({ phase: 'uploading', upload: { loaded: 2100 * MB, total: 2100 * MB, speed: 18 * MB } })} />);
    const status = screen.getByRole('status', { name: 'Subiendo el video' });
    expect(within(status).getByText('Creando el proyecto…')).toBeInTheDocument();
    expect(within(status).getByText('100 %')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancelar' })).not.toBeInTheDocument();
    expect(screen.getByText('El video ya está subido. En un momento se abre el proyecto.')).toBeInTheDocument();
  });

  it('en reposo muestra el costo, el saldo después, el tiempo estimado y Crear proyecto', async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();
    render(<SummaryCard {...summaryProps({ onCreate })} />);
    expect(screen.getByTestId('cost-total')).toHaveTextContent('1.944');
    expect(screen.getByText('2.916')).toBeInTheDocument();
    expect(screen.getByText('≈ 12 min')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Crear proyecto' }));
    expect(onCreate).toHaveBeenCalledOnce();
  });

  it('un error al crear queda arriba del botón', () => {
    render(<SummaryCard {...summaryProps({ error: 'No pudimos conectar. Revisá tu conexión y probá de nuevo.' })} />);
    expect(screen.getByRole('alert')).toHaveTextContent('No pudimos conectar');
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeEnabled();
  });

  it('subida cortada: lo dice junto al archivo y ofrece reintentar', async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    const state: FileState = { file: new File(['x'], 'podcast-ep42.mp4', { type: 'video/mp4' }), info: { duration: 4360, width: 1920, height: 1080 }, problem: null };
    renderScreen(<FilePicker state={state} onPick={vi.fn()} cut={{ percent: 64, loaded: 1344 * MB, delivered: false }} onRetry={onRetry} busy={false} plan={null} plans={[]} pricing={null} />);
    const row = screen.getByTestId('file-row');
    expect(within(row).getByRole('alert')).toHaveTextContent('Se cortó la subida al 64 %.');
    expect(within(row).getByText(/No se creó el proyecto ni se reservaron tokens/)).toBeInTheDocument();
    expect(within(row).getByRole('progressbar', { name: 'Subida detenida' })).toHaveAttribute('aria-valuenow', '64');
    expect(within(row).getByRole('button', { name: 'Elegir otro archivo' })).toBeInTheDocument();
    await user.click(within(row).getByRole('button', { name: 'Reintentar' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe('C1 · subida cortada con el video ya enviado', () => {
  it('no afirma que no se creó nada: manda a revisar los proyectos', () => {
    const state: FileState = { file: new File(['x'], 'podcast-ep42.mp4', { type: 'video/mp4' }), info: { duration: 4360, width: 1920, height: 1080 }, problem: null };
    renderScreen(<FilePicker state={state} onPick={vi.fn()} cut={{ percent: 100, loaded: 2100 * MB, delivered: true }} onRetry={vi.fn()} busy={false} plan={null} plans={[]} pricing={null} />);
    const row = screen.getByTestId('file-row');
    expect(within(row).getByRole('alert')).toHaveTextContent('Se cortó la conexión cuando el video ya estaba subido.');
    expect(within(row).getByText(/Puede que el proyecto se haya creado/)).toBeInTheDocument();
    expect(within(row).getByRole('link', { name: 'revisá tus proyectos' })).toHaveAttribute('href', '/proyectos');
    expect(within(row).queryByText(/No se creó el proyecto/)).not.toBeInTheDocument();
  });
});

describe('C1 · dibujo del layout', () => {
  it('Versus en 1:1 y 16:9 deja negro a los costados; en 9:16 usa las dos mitades', () => {
    const { container, rerender } = render(<LayoutDiagram layout="versus" ratio="9:16" />);
    expect(container.querySelectorAll('[data-layout="versus"] > span')).toHaveLength(2);
    rerender(<LayoutDiagram layout="versus" ratio="16:9" />);
    const shapes = container.querySelectorAll('[data-layout="versus"] > span');
    expect(shapes).toHaveLength(3);
    expect(shapes[0]).toHaveClass('dgBlack');
    rerender(<LayoutDiagram layout="versus" ratio="1:1" />);
    expect(container.querySelector('[data-layout="versus"] > span')).toHaveClass('dgBlack');
  });
});
