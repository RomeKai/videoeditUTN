import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Button } from './Button';
import { Dialog, Drawer } from './Dialog';

function Harness({ dismissable = true, onClose }: { dismissable?: boolean; onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Abrir
      </button>
      <button type="button">Afuera</button>
      <Dialog
        open={open}
        dismissable={dismissable}
        onClose={() => {
          onClose?.();
          setOpen(false);
        }}
        title="Renombrar proyecto"
        description="El nombre se ve en la lista."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancelar
            </Button>
            <Button variant="primary">Guardar</Button>
          </>
        }
      >
        <input aria-label="Título" data-autofocus />
      </Dialog>
    </>
  );
}

describe('Dialog', () => {
  it('se anuncia como diálogo modal con título y descripción', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Abrir' }));
    const dialog = screen.getByRole('dialog', { name: 'Renombrar proyecto' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleDescription('El nombre se ve en la lista.');
  });

  it('lleva el foco adentro, lo atrapa con Tab y Shift+Tab, y lo devuelve al cerrar', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Abrir' });
    await user.click(opener);
    const field = screen.getByRole('textbox', { name: 'Título' });
    // The element marked with data-autofocus gets the initial focus.
    expect(field).toHaveFocus();

    await user.tab();
    expect(screen.getByRole('button', { name: 'Cancelar' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Guardar' })).toHaveFocus();
    // Past the last element it wraps to the first: focus never leaves the dialog.
    await user.tab();
    expect(field).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole('button', { name: 'Guardar' })).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Afuera' })).not.toHaveFocus();

    await user.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('Escape lo cierra', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: 'Abrir' }));
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('el fondo lo cierra, pero un clic adentro no', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: 'Abrir' }));
    const dialog = screen.getByRole('dialog');
    await user.click(dialog);
    expect(onClose).not.toHaveBeenCalled();
    await user.click(dialog.parentElement as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('si no es descartable, ni Escape ni el fondo lo cierran', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness dismissable={false} onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: 'Abrir' }));
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('dialog').parentElement as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('con dos abiertos, Escape cierra solo el de arriba', async () => {
    const user = userEvent.setup();
    const closeBelow = vi.fn();
    const closeAbove = vi.fn();
    render(
      <>
        <Dialog open onClose={closeBelow} title="Abajo" />
        <Dialog open onClose={closeAbove} title="Arriba">
          <button type="button">Ok</button>
        </Dialog>
      </>,
    );
    await user.keyboard('{Escape}');
    expect(closeAbove).toHaveBeenCalledTimes(1);
    expect(closeBelow).not.toHaveBeenCalled();
  });

  it('bloquea el scroll de la página mientras está abierto', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Abrir' }));
    expect(document.body.style.overflow).toBe('hidden');
    await user.keyboard('{Escape}');
    expect(document.body.style.overflow).toBe('');
  });
});

describe('Drawer', () => {
  it('es un panel modal con botón Cerrar y las mismas reglas de foco', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose} title="Publicar clip" footer={<button type="button">Programar</button>}>
        <input aria-label="Texto" />
      </Drawer>,
    );
    expect(screen.getByRole('dialog', { name: 'Publicar clip' })).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('button', { name: 'Cerrar' })).toHaveFocus();
    await user.tab();
    await user.tab();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Cerrar' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
