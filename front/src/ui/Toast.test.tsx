import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MAX_TOASTS, TOAST_DURATION_MS, ToastProvider, useToast } from './Toast';

function Buttons({ onRetry }: { onRetry?: () => void }) {
  const toast = useToast();
  return (
    <>
      <button type="button" onClick={() => toast.success('Clip descargado')}>
        ok
      </button>
      <button type="button" onClick={() => toast.error('No pudimos descargar el clip', { action: { label: 'Reintentar', onClick: onRetry } })}>
        error
      </button>
      <button type="button" onClick={() => toast.show('Tercero')}>
        otro
      </button>
      <button type="button" onClick={() => toast.show('Fijo', { duration: 0 })}>
        fijo
      </button>
    </>
  );
}

describe('Toast', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('confirma con role=status y se va solo a los 5 segundos', () => {
    render(
      <ToastProvider>
        <Buttons />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText('ok'));
    expect(screen.getByRole('status')).toHaveTextContent('Clip descargado');
    act(() => vi.advanceTimersByTime(TOAST_DURATION_MS - 1));
    expect(screen.getByRole('status')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('un error usa role=alert y su acción lo cierra', () => {
    const onRetry = vi.fn();
    render(
      <ToastProvider>
        <Buttons onRetry={onRetry} />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText('error'));
    expect(screen.getByRole('alert')).toHaveTextContent('No pudimos descargar el clip');
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('con una acción espera mientras el puntero o el foco están encima; la región se anuncia', () => {
    render(
      <ToastProvider>
        <Buttons />
      </ToastProvider>,
    );
    expect(screen.getByRole('region', { name: 'Avisos' })).toHaveAttribute('aria-live', 'polite');
    fireEvent.click(screen.getByText('error'));
    const toast = screen.getByRole('alert');
    fireEvent.mouseEnter(toast);
    act(() => vi.advanceTimersByTime(TOAST_DURATION_MS * 3));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    fireEvent.mouseLeave(toast);
    fireEvent.focus(screen.getByRole('button', { name: 'Reintentar' }));
    act(() => vi.advanceTimersByTime(TOAST_DURATION_MS * 3));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    fireEvent.blur(screen.getByRole('button', { name: 'Reintentar' }));
    act(() => vi.advanceTimersByTime(TOAST_DURATION_MS));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('muestra dos como máximo: el más viejo sale', () => {
    render(
      <ToastProvider>
        <Buttons />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText('ok'));
    fireEvent.click(screen.getByText('error'));
    fireEvent.click(screen.getByText('otro'));
    const region = screen.getByRole('region', { name: 'Avisos' });
    expect(region.children).toHaveLength(MAX_TOASTS);
    expect(region).not.toHaveTextContent('Clip descargado');
    expect(region).toHaveTextContent('Tercero');
  });

  it('se puede cerrar a mano y uno sin duración no se va solo', () => {
    render(
      <ToastProvider>
        <Buttons />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText('fijo'));
    act(() => vi.advanceTimersByTime(TOAST_DURATION_MS * 3));
    expect(screen.getByRole('status')).toHaveTextContent('Fijo');
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar aviso' }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('useToast fuera del provider avisa cómo arreglarlo', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<Buttons />)).toThrow(/ToastProvider/);
    spy.mockRestore();
  });
});
