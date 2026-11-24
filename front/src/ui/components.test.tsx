import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Badge } from './Badge';
import { Banner } from './Banner';
import { Button, IconButton } from './Button';
import { Checkbox } from './Checkbox';
import { DropdownMenu } from './DropdownMenu';
import { FilterChips } from './FilterChip';
import { NetworkChip } from './NetworkChip';
import { DateTime, Tokens } from './Numeric';
import { Pagination } from './Pagination';
import { Popover } from './Popover';
import { ProgressBar, StepSegments } from './Progress';
import { Radio, RadioCard, RadioGroup } from './Radio';
import { Score, scoreBand } from './Score';
import { SegmentedControl } from './SegmentedControl';
import { StageStepper } from './StageStepper';
import { Switch } from './Switch';
import { TabList, TabPanel } from './Tabs';
import { Tooltip } from './Tooltip';
import { VideoPlayer } from './VideoPlayer';
import { VideoThumb } from './VideoThumb';

describe('Button', () => {
  it('cargando no dispara el clic y se anuncia ocupado', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button variant="primary" loading onClick={onClick}>
        Creando…
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Creando…' });
    expect(button).toHaveAttribute('aria-busy', 'true');
    await user.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('bloqueado por rol: sigue visible y enfocable, explica el motivo y no actúa', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button variant="primary" disabledReason="Pedile acceso a un admin" onClick={onClick}>
        Nuevo proyecto
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Nuevo proyecto' });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).not.toBeDisabled();
    await user.tab();
    expect(button).toHaveFocus();
    expect(screen.getByRole('tooltip')).toHaveTextContent('Pedile acceso a un admin');
    expect(button).toHaveAccessibleDescription('Pedile acceso a un admin');
    await user.keyboard('{Enter}');
    expect(onClick).not.toHaveBeenCalled();
  });

  it('con href es un link, y deshabilitado deja de serlo', () => {
    const { rerender } = render(<Button href="/uso/planes">Ver planes</Button>);
    expect(screen.getByRole('link', { name: 'Ver planes' })).toHaveAttribute('href', '/uso/planes');
    rerender(
      <Button href="/uso/planes" disabled>
        Ver planes
      </Button>,
    );
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ver planes' })).toBeDisabled();
  });

  it('el botón de ícono siempre tiene nombre', () => {
    render(<IconButton icon="more" label="Más opciones" />);
    expect(screen.getByRole('button', { name: 'Más opciones' })).toBeInTheDocument();
  });
});

describe('controles', () => {
  it('interruptor, tilde y radio cambian con clic y con teclado', async () => {
    const user = userEvent.setup();
    function Form() {
      const [on, setOn] = useState(false);
      const [level, setLevel] = useState('fast');
      return (
        <>
          <Switch checked={on} onChange={setOn} label="Seguir caras" />
          <Checkbox label="Acepto los términos" />
          <RadioGroup ariaLabel="Nivel de IA" value={level} onChange={setLevel}>
            <Radio value="fast" label="Rápido" />
            <Radio value="smart" label="Inteligente" />
          </RadioGroup>
        </>
      );
    }
    render(<Form />);
    const toggle = screen.getByRole('switch', { name: 'Seguir caras' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    toggle.focus();
    await user.keyboard(' ');
    expect(toggle).toHaveAttribute('aria-checked', 'false');

    await user.click(screen.getByRole('checkbox', { name: 'Acepto los términos' }));
    expect(screen.getByRole('checkbox', { name: 'Acepto los términos' })).toBeChecked();

    await user.click(screen.getByRole('radio', { name: 'Inteligente' }));
    expect(screen.getByRole('radio', { name: 'Inteligente' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Rápido' })).not.toBeChecked();
  });

  it('una opción bloqueada por plan se ve, no se puede elegir y dice por qué', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <>
        <SegmentedControl
          ariaLabel="Resolución"
          value="720p"
          onChange={onChange}
          options={[
            { value: '720p', label: '720p' },
            { value: '4K', label: '4K', lockedReason: 'Disponible en el plan Pro' },
          ]}
        />
        <RadioGroup ariaLabel="Estilo" value="dynamic" onChange={onChange} layout="cards">
          <RadioCard value="dynamic" title="Dinámico" />
          <RadioCard value="hormozi" title="Hormozi" lockedReason="Disponible en el plan Creador" />
        </RadioGroup>
      </>,
    );
    expect(screen.getByRole('button', { name: '720p' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: '4K' }));
    await user.click(screen.getByRole('radio', { name: /Hormozi/ }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole('radio', { name: /Dinámico/ })).toHaveAttribute('aria-checked', 'true');
    await user.hover(screen.getByRole('button', { name: '4K' }));
    expect(screen.getByRole('tooltip')).toHaveTextContent('Disponible en el plan Pro');
  });

  it('los chips de filtro eligen uno y ocultan «Con error» si no hay', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <FilterChips
        ariaLabel="Filtrar por estado"
        value="all"
        onChange={onChange}
        options={[
          { value: 'all', label: 'Todos', count: 8 },
          { value: 'ready', label: 'Listos', count: 3 },
          { value: 'failed', label: 'Con error', count: 0, hideWhenEmpty: true },
        ]}
      />,
    );
    expect(screen.getByRole('button', { name: /Todos/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: /Con error/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Listos/ }));
    expect(onChange).toHaveBeenCalledWith('ready');
  });
});

describe('menús y flotantes', () => {
  it('el menú se recorre con flechas, activa con Enter y vuelve al disparador', async () => {
    const user = userEvent.setup();
    const onRename = vi.fn();
    const onDelete = vi.fn();
    render(
      <DropdownMenu
        ariaLabel="Acciones del proyecto"
        trigger={(props) => (
          <button type="button" {...props}>
            Más acciones
          </button>
        )}
        items={[
          { key: 'rename', label: 'Renombrar', onSelect: onRename },
          { key: 'publish', label: 'Publicar', disabled: true, disabledReason: 'Pedile acceso a un admin' },
          { key: 'delete', label: 'Borrar', danger: true, onSelect: onDelete, separatorBefore: true },
        ]}
      />,
    );
    const trigger = screen.getByRole('button', { name: 'Más acciones' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    await user.click(trigger);
    const menu = screen.getByRole('menu', { name: 'Acciones del proyecto' });
    expect(within(menu).getByRole('menuitem', { name: 'Renombrar' })).toHaveFocus();
    // The disabled item is skipped.
    await user.keyboard('{ArrowDown}');
    expect(within(menu).getByRole('menuitem', { name: 'Borrar' })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(onRename).not.toHaveBeenCalled();
  });

  it('el popover se cierra con Escape y con un clic afuera', async () => {
    const user = userEvent.setup();
    render(
      <>
        <Popover
          ariaLabel="Filtros"
          trigger={(props) => (
            <button type="button" {...props}>
              Filtrar
            </button>
          )}
        >
          <button type="button">Adentro</button>
        </Popover>
        <button type="button">Afuera</button>
      </>,
    );
    await user.click(screen.getByRole('button', { name: 'Filtrar' }));
    expect(screen.getByRole('dialog', { name: 'Filtros' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Adentro' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Filtrar' })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'Filtrar' }));
    await user.click(screen.getByRole('button', { name: 'Afuera' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('el tooltip aparece al pasar el mouse y al enfocar', async () => {
    const user = userEvent.setup();
    render(
      <Tooltip content="2.617,65 reservados">
        <button type="button">Saldo</button>
      </Tooltip>,
    );
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    await user.hover(screen.getByRole('button'));
    expect(screen.getByRole('tooltip')).toHaveTextContent('2.617,65 reservados');
    await user.unhover(screen.getByRole('button'));
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('las pestañas en página se mueven con las flechas', async () => {
    const user = userEvent.setup();
    function Tabs() {
      const [tab, setTab] = useState<'week' | 'month'>('week');
      return (
        <>
          <TabList
            ariaLabel="Vista"
            value={tab}
            onChange={setTab}
            items={[
              { value: 'week', label: 'Semana' },
              { value: 'month', label: 'Mes' },
            ]}
          />
          <TabPanel value="week" active={tab === 'week'}>
            Panel semana
          </TabPanel>
          <TabPanel value="month" active={tab === 'month'}>
            Panel mes
          </TabPanel>
        </>
      );
    }
    render(<Tabs />);
    expect(screen.getByRole('tab', { name: 'Semana' })).toHaveAttribute('aria-selected', 'true');
    screen.getByRole('tab', { name: 'Semana' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Mes' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Panel mes');
  });
});

describe('estado y datos', () => {
  it('etiquetas, avisos, progreso y pasos se anuncian bien', () => {
    render(
      <>
        <Badge tone="accent" dot>
          Listo para revisar
        </Badge>
        <Banner tone="err" title="No pudimos conectar" action={<button type="button">Reintentar</button>} />
        <Banner tone="info" title="Incluye 10 % de descuento en tokens">
          Ya está aplicado.
        </Banner>
        <ProgressBar label="Subiendo" value={64} />
        <StepSegments done={1} />
        <StageStepper
          steps={[
            { key: 'a', label: 'Preparar', state: 'done' },
            { key: 'b', label: 'Transcribir', state: 'run', detail: 'En curso · Paso 2 de 4' },
            { key: 'c', label: 'Renderizar', state: 'pending', separatorBefore: 'Después de tu revisión' },
          ]}
        />
      </>,
    );
    expect(screen.getByText('Listo para revisar')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('No pudimos conectar');
    expect(screen.getByRole('status')).toHaveTextContent('Incluye 10 % de descuento en tokens');
    expect(screen.getByRole('progressbar', { name: 'Subiendo' })).toHaveAttribute('aria-valuenow', '64');
    expect(screen.getByRole('img', { name: 'Paso 2 de 4' })).toBeInTheDocument();
    const steps = screen.getByRole('list', { name: 'Proceso' });
    expect(within(steps).getByText('Después de tu revisión')).toBeInTheDocument();
    expect(within(steps).getByText('Transcribir').closest('[aria-current="step"]')).not.toBeNull();
  });

  it('el puntaje lleva su banda', () => {
    expect([scoreBand(92), scoreBand(76), scoreBand(58)]).toEqual(['Excelente', 'Bueno', 'Flojo']);
    render(<Score value={92} />);
    expect(screen.getByLabelText('Puntaje 92 de 100: excelente')).toBeInTheDocument();
  });

  it('la red siempre lleva su nombre; sin texto, lo lleva como etiqueta', () => {
    render(
      <>
        <NetworkChip platform="TIKTOK" />
        <NetworkChip platform="INSTAGRAM_REELS" hideName />
      </>,
    );
    expect(screen.getByText('TikTok')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Instagram Reels' })).toBeInTheDocument();
  });

  it('miniatura con duración y puntaje; tokens y fechas con su formato', async () => {
    render(
      <>
        <VideoThumb ratio="9:16" seed="clip" duration={31} score={91} />
        <Tokens value="1404.9000000000" />
        <Tokens value={1755} signed unit={false} />
        <DateTime value={new Date(2026, 10, 24, 14, 30)} />
      </>,
    );
    expect(screen.getByText('0:31')).toBeInTheDocument();
    expect(screen.getByLabelText('Puntaje 91 de 100')).toBeInTheDocument();
    expect(screen.getByText('1.404,90 tokens')).toBeInTheDocument();
    expect(screen.getByText('+1.755')).toBeInTheDocument();
    expect(await screen.findByText('24 nov 2026, 14:30')).toBeInTheDocument();
  });

  it('la paginación resume y avisa el cambio de página', async () => {
    const user = userEvent.setup();
    const onPageChange = vi.fn();
    render(<Pagination page={2} pageSize={25} count={64} onPageChange={onPageChange} noun="proyectos" />);
    expect(screen.getByText('26–50 de 64 proyectos')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Siguiente' }));
    expect(onPageChange).toHaveBeenCalledWith(3);
    await user.click(screen.getByRole('button', { name: 'Anterior' }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });
});

describe('VideoPlayer', () => {
  it('sin video usa el fotograma de muestra y responde a los atajos', async () => {
    const user = userEvent.setup();
    const onMarkIn = vi.fn();
    const onMarkOut = vi.fn();
    const onTimeUpdate = vi.fn();
    render(<VideoPlayer label="Reproductor" duration={120} onMarkIn={onMarkIn} onMarkOut={onMarkOut} onTimeUpdate={onTimeUpdate} />);
    const player = screen.getByRole('group', { name: 'Reproductor' });
    const seek = within(player).getByRole('slider', { name: 'Posición' });
    seek.focus();
    await user.keyboard('{ArrowRight}');
    expect(onTimeUpdate).toHaveBeenLastCalledWith(5);
    await user.keyboard('{Shift>}{ArrowLeft}{/Shift}');
    expect(onTimeUpdate).toHaveBeenLastCalledWith(4);
    await user.keyboard('i');
    expect(onMarkIn).toHaveBeenCalledWith(4);
    await user.keyboard('o');
    expect(onMarkOut).toHaveBeenCalledWith(4);
    await user.keyboard(' ');
    expect(within(player).getByRole('button', { name: 'Pausar' })).toBeInTheDocument();
  });
});
