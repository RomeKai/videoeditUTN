import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { hydrateRoot } from 'react-dom/client';
import { renderToString } from 'react-dom/server';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Button } from './Button';
import { Dialog } from './Dialog';
import { DropdownMenu } from './DropdownMenu';
import { Popover } from './Popover';
import { Tooltip } from './Tooltip';

describe('la trampa de foco del diálogo sigue con algo abierto adentro', () => {
  it('Tab desde el último control con su tooltip abierto vuelve al primero; al cerrar, el foco vuelve a quien lo abrió', async () => {
    const user = userEvent.setup();
    function Case() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Abrir
          </button>
          <Dialog open={open} onClose={() => setOpen(false)} title="Con tooltip">
            <input aria-label="Nombre" data-autofocus />
            <Button disabledReason="Pedile acceso a un admin">Guardar</Button>
          </Dialog>
        </>
      );
    }
    render(<Case />);
    await user.click(screen.getByRole('button', { name: 'Abrir' }));
    const dialog = screen.getByRole('dialog');
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>('button, input'));
    // Walk to the last control: its tooltip opens on focus and becomes the top layer.
    act(() => (focusable[focusable.length - 1] as HTMLElement).focus());
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(focusable[0]);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(focusable[focusable.length - 1]);
    // Escape hides the tooltip first, then closes the dialog and gives the focus back.
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Abrir' })).toHaveFocus();
  });

  it('con un menú o un popover abierto como último elemento, Tab no sale del diálogo', async () => {
    const user = userEvent.setup();
    render(
      <>
        <button type="button">Detrás</button>
        <Dialog open onClose={() => {}} title="Con menú al final">
          <input aria-label="Nombre" />
          <Popover ariaLabel="Detalle" trigger={(props) => <Button {...props}>Ver detalle</Button>}>
            <button type="button">Adentro</button>
          </Popover>
        </Dialog>
      </>,
    );
    const dialog = screen.getByRole('dialog');
    await user.click(screen.getByRole('button', { name: 'Ver detalle' }));
    expect(screen.getByRole('button', { name: 'Adentro' })).toBeInTheDocument();
    for (let step = 0; step < 6; step += 1) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    expect(screen.getByRole('button', { name: 'Detrás' })).not.toHaveFocus();
  });
});

describe('capas anidadas: Escape solo actúa sobre la de arriba', () => {
  it('un menú abierto dentro de un diálogo se cierra primero; el diálogo, con el segundo Escape', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} title="Con menú">
        <DropdownMenu
          ariaLabel="Acciones"
          items={[
            { key: 'a', label: 'Uno' },
            { key: 'b', label: 'Dos' },
          ]}
          trigger={(props) => <Button {...props}>Más</Button>}
        />
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Más' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Más' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('lo mismo con un popover', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} title="Con popover">
        <Popover ariaLabel="Filtro" trigger={(props) => <Button {...props}>Filtro</Button>}>
          <button type="button">Dentro</button>
        </Popover>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Filtro' }));
    expect(screen.getByRole('dialog', { name: 'Filtro' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Filtro' })).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('y con un tooltip: Escape lo oculta sin cerrar el diálogo', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} title="Con tooltip">
        <Tooltip content="Pedile acceso a un admin">
          <button type="button">Publicar</button>
        </Tooltip>
      </Dialog>,
    );
    await user.hover(screen.getByRole('button', { name: 'Publicar' }));
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('Tab dentro del menú lo cierra sin que el diálogo pierda el foco', async () => {
    const user = userEvent.setup();
    render(
      <Dialog open onClose={() => {}} title="Con menú">
        <DropdownMenu ariaLabel="Acciones" items={[{ key: 'a', label: 'Uno' }]} trigger={(props) => <Button {...props}>Más</Button>} />
        <button type="button">Otro</button>
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: 'Más' }));
    await user.tab();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Con menú' }).contains(document.activeElement)).toBe(true);
  });
});

describe('diálogo abierto en el primer render', () => {
  it('después de hidratar tiene foco adentro, bloqueo de scroll y Escape', async () => {
    const onClose = vi.fn();
    const app = (
      <div>
        <button type="button">fuera</button>
        <Dialog open onClose={onClose} title="Abierto al cargar">
          <button type="button">dentro</button>
        </Dialog>
      </div>
    );
    const container = document.createElement('div');
    container.innerHTML = renderToString(app);
    document.body.appendChild(container);
    let root: ReturnType<typeof hydrateRoot> | undefined;
    await act(async () => {
      root = hydrateRoot(container, app);
    });
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.contains(document.activeElement)).toBe(true);
    expect(document.body.style.overflow).toBe('hidden');
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    await act(async () => root?.unmount());
    container.remove();
    expect(document.body.style.overflow).toBe('');
  });
});
