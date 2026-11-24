'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useProjects } from '@/api/hooks';
import { useDebounced } from '@/lib/hooks';
import { describeProjectStatus } from '@/lib/project-status';
import { useTheme } from '@/lib/theme';
import { Badge } from '@/ui/Badge';
import { useIsClient } from '@/ui/Dialog';
import { Icon, type IconName } from '@/ui/Icon';
import { Kbd } from '@/ui/Kbd';
import { useModalLayer } from '@/ui/overlay';
import styles from './AppShell.module.css';
import { MAIN_NAV } from './nav';
import { usePermissions, useSession } from './session';

interface Command {
  key: string;
  group: 'Proyectos' | 'Ir a' | 'Acciones' | 'Workspaces';
  label: string;
  icon: IconName;
  run: () => void;
  hint?: ReactNode;
  badge?: ReactNode;
}

function highlight(text: string, query: string): ReactNode {
  const index = query ? text.toLowerCase().indexOf(query.toLowerCase()) : -1;
  if (index < 0) return text;
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + query.length)}</mark>
      {text.slice(index + query.length)}
    </>
  );
}

/**
 * J1 · Menú de comandos (⌘K / Ctrl+K): search projects by title, go to any
 * section and run the global actions. Arrows move, Enter opens, Escape closes.
 */
export function CommandMenu({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const router = useRouter();
  const { toggle: toggleTheme } = useTheme();
  const { me, workspace, switchWorkspace } = useSession();
  const { can } = usePermissions();
  const isClient = useIsClient();
  const ref = useModalLayer<HTMLDivElement>({ open, onClose });
  const search = useDebounced(query.trim(), 200);
  const projects = useProjects({ search, page_size: 5 }, { enabled: open && search.length > 0 });

  useEffect(() => {
    if (!open) {
      setQuery('');
      setSelected(0);
    }
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const go = (href: string) => () => {
      onClose();
      router.push(href);
    };
    const text = query.trim().toLowerCase();
    const matches = (label: string) => !text || label.toLowerCase().includes(text);
    const found: Command[] = (search ? (projects.data?.results ?? []) : []).map((project) => {
      const view = describeProjectStatus(project);
      return {
        key: `project-${project.id}`,
        group: 'Proyectos',
        label: project.title,
        icon: 'video',
        run: go(view.action === 'review' ? `/proyectos/${project.id}/revision` : `/proyectos/${project.id}`),
        badge: (
          <Badge tone={view.tone} dot>
            {view.label}
          </Badge>
        ),
      };
    });
    const sections: Command[] = [
      ...MAIN_NAV.map((item) => ({ key: `go-${item.href}`, group: 'Ir a' as const, label: item.label, icon: item.icon, run: go(item.href) })),
      { key: 'go-perfil', group: 'Ir a' as const, label: 'Perfil', icon: 'user' as const, run: go('/perfil') },
    ].filter((item) => matches(item.label));
    const actions: Command[] = [
      ...(can('project.create') ? [{ key: 'new', group: 'Acciones' as const, label: 'Nuevo proyecto', icon: 'plus' as const, run: go('/proyectos/nuevo'), hint: <Kbd>N</Kbd> }] : []),
      ...(can('billing.manage') ? [{ key: 'buy', group: 'Acciones' as const, label: 'Comprar tokens', icon: 'tokens' as const, run: go('/uso/comprar'), hint: <Kbd>T</Kbd> }] : []),
      {
        key: 'theme',
        group: 'Acciones' as const,
        label: 'Cambiar tema',
        icon: 'moon' as const,
        run: () => {
          toggleTheme();
          onClose();
        },
      },
    ].filter((item) => matches(item.label));
    const others: Command[] = (me?.workspaces ?? [])
      .filter((item) => item.id !== workspace?.id)
      .map((item) => ({
        key: `ws-${item.id}`,
        group: 'Workspaces' as const,
        label: `Cambiar a ${item.name}`,
        icon: 'selector' as const,
        run: () => {
          onClose();
          void switchWorkspace(item.id).then(() => router.push('/proyectos'));
        },
      }))
      .filter((item) => matches(item.label) || matches('Cambiar de workspace'));
    return [...found, ...sections, ...actions, ...others];
  }, [can, me?.workspaces, onClose, projects.data?.results, query, router, search, switchWorkspace, toggleTheme, workspace?.id]);

  useEffect(() => {
    setSelected((index) => Math.min(index, Math.max(0, commands.length - 1)));
  }, [commands.length]);

  if (!open || !isClient) return null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setSelected((index) => (commands.length ? (index + 1) % commands.length : 0));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setSelected((index) => (commands.length ? (index - 1 + commands.length) % commands.length : 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      commands[selected]?.run();
    }
  };

  let lastGroup = '';
  return createPortal(
    <div
      className={styles.cmdBackdrop}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div ref={ref} className={styles.cmdk} role="dialog" aria-modal="true" aria-label="Menú de comandos" tabIndex={-1} onKeyDown={onKeyDown}>
        <div className={styles.cmdIn}>
          <Icon name="search" />
          <label className="sr-only" htmlFor="command-menu-input">
            Buscar o ejecutar un comando
          </label>
          <input
            id="command-menu-input"
            className={styles.cmdInput}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setSelected(0);
            }}
            placeholder="Buscá un proyecto o una acción…"
            autoComplete="off"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-menu-list"
            aria-activedescendant={commands[selected] ? `command-${commands[selected].key}` : undefined}
            data-autofocus
          />
          <Kbd>esc</Kbd>
        </div>
        <div className={styles.cmdList} id="command-menu-list" role="listbox" aria-label="Resultados">
          {commands.length === 0 ? (
            <p className="t-small muted" style={{ padding: '16px 10px' }}>
              {projects.isFetching ? 'Buscando…' : 'No encontramos nada con ese texto.'}
            </p>
          ) : null}
          {commands.map((command, index) => {
            const heading = command.group !== lastGroup ? command.group : null;
            lastGroup = command.group;
            return (
              <div key={command.key} style={{ display: 'contents' }}>
                {heading ? <p className={`t-over ${styles.cmdGroup}`}>{heading}</p> : null}
                <button
                  type="button"
                  id={`command-${command.key}`}
                  role="option"
                  aria-selected={index === selected}
                  className={styles.cmdRow}
                  tabIndex={-1}
                  onMouseMove={() => setSelected(index)}
                  onClick={command.run}
                >
                  <Icon name={command.icon} />
                  <span className={styles.cmdText}>{command.group === 'Proyectos' ? highlight(command.label, query.trim()) : command.label}</span>
                  {command.badge}
                  {command.hint}
                  {index === selected ? <Kbd>↵</Kbd> : null}
                </button>
              </div>
            );
          })}
        </div>
        <div className={styles.cmdFoot}>
          <span>
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> Moverse
          </span>
          <span>
            <Kbd>↵</Kbd> Abrir
          </span>
          <span>
            <Kbd>esc</Kbd> Cerrar
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
