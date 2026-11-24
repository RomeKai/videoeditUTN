'use client';

import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { formatDuration, formatTimecode } from '@/lib/format';
import { Button, EmptyState, Icon, IconButton, Input } from '@/ui';
import { normalize, textOf, type Row, type Word } from '../lib/transcript';
import styles from './transcript.module.css';

export interface TranscriptClip {
  n: number;
  start: number;
  end: number;
}

export interface TranscriptPanelProps {
  words: ReadonlyArray<Word>;
  rows: ReadonlyArray<Row>;
  /** Selected clip: its stretch is tinted and its borders are marked. */
  clip: TranscriptClip | null;
  /** Index of the word being said, -1 in a silence. */
  activeWord: number;
  /** False for a Lector: the text can be read and played, not turned into clips. */
  canEdit: boolean;
  /** "main": the transcript is the main surface (review without suggestions). */
  variant?: 'side' | 'main';
  /** On the phone the page scrolls, not the panel, and actions are full-width buttons. */
  mobile?: boolean;
  onSeek: (seconds: number) => void;
  onCreateClip: (start: number, end: number, text: string) => void;
  onMoveEdge: (edge: 'start' | 'end', seconds: number) => void;
  onFitClip: (start: number, end: number) => void;
}

interface RowViewProps {
  row: Row;
  words: ReadonlyArray<Word>;
  active: number;
  selFrom: number;
  selTo: number;
  inFrom: number;
  inTo: number;
  found: boolean;
  current: boolean;
  onWord: (index: number) => void;
}

/** One paragraph. Memoized: playing only re-renders the row that holds the current word. */
const RowView = memo(function RowView({ row, words, active, selFrom, selTo, inFrom, inTo, found, current, onWord }: RowViewProps) {
  const spans: ReactNode[] = [];
  for (let index = row.from; index <= row.to; index += 1) {
    const word = words[index] as Word;
    const selected = index >= selFrom && index <= selTo;
    spans.push(
      <span
        key={index}
        data-w={index}
        className={cx(styles.w, index >= inFrom && index <= inTo && styles.in, selected && styles.sel, index === active && styles.now)}
        onClick={() => onWord(index)}
      >
        {word.text}{' '}
      </span>,
    );
  }
  return (
    <div className={cx(styles.row, found && styles.found, current && styles.foundCurrent)} data-row={row.index}>
      <span className={cx('timecode', styles.ts)}>{formatTimecode(row.start)}</span>
      <p>{spans}</p>
    </div>
  );
});

function wordIndexOf(node: Node | null): number | null {
  const element = node instanceof Element ? node : (node?.parentElement ?? null);
  const span = element?.closest?.('[data-w]');
  const value = span?.getAttribute('data-w');
  return value === null || value === undefined ? null : Number(value);
}

/**
 * Transcript in sync with the player. Selecting words opens the actions to
 * make a clip or move a border of the current one; borders always land on
 * whole words because the selection is turned into word indexes.
 */
export function TranscriptPanel({
  words,
  rows,
  clip,
  activeWord,
  canEdit,
  variant = 'side',
  mobile = false,
  onSeek,
  onCreateClip,
  onMoveEdge,
  onFitClip,
}: TranscriptPanelProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<{ from: number; to: number } | null>(null);
  const [searching, setSearching] = useState(variant === 'main');
  const [query, setQuery] = useState('');
  const [hit, setHit] = useState(0);

  const inRange = useMemo(() => {
    if (!clip) return null;
    let from = -1;
    let to = -1;
    for (let index = 0; index < words.length; index += 1) {
      const word = words[index] as Word;
      if (word.end <= clip.start + 0.01) continue;
      if (word.start >= clip.end - 0.01) break;
      if (from === -1) from = index;
      to = index;
    }
    return from === -1 ? null : { from, to };
  }, [clip, words]);

  const matches = useMemo(() => {
    const needle = normalize(query.trim());
    if (needle.length < 2) return [];
    return rows.filter((row) => normalize(textOf(words, row.from, row.to)).includes(needle)).map((row) => row.index);
  }, [query, rows, words]);

  const scrollToRow = (rowIndex: number) => {
    const container = scroller.current;
    const target = container?.querySelector<HTMLElement>(`[data-row="${rowIndex}"]`);
    if (!container || !target) return;
    if (!mobile) container.scrollTop = Math.max(0, container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top - 40);
    else target.scrollIntoView?.({ block: 'center' });
  };

  // Follow the selected clip: bring its first paragraph into view (inside the panel only, never the page).
  const clipKey = clip ? `${clip.n}` : '';
  useEffect(() => {
    if (!inRange || mobile || variant !== 'side') return;
    const row = rows.find((item) => item.to >= inRange.from);
    if (row) scrollToRow(row.index);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipKey, rows]);

  useEffect(() => {
    setHit(0);
    if (matches[0] !== undefined) scrollToRow(matches[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [matches]);

  const goToHit = (delta: number) => {
    if (matches.length === 0) return;
    const next = (hit + delta + matches.length) % matches.length;
    setHit(next);
    scrollToRow(matches[next] as number);
  };

  /** Turns the browser's text selection into a range of whole words. */
  const readSelection = () => {
    const current = window.getSelection?.();
    if (!current || current.isCollapsed || current.rangeCount === 0) return;
    const range = current.getRangeAt(0);
    if (!scroller.current?.contains(range.commonAncestorContainer)) return;
    const a = wordIndexOf(range.startContainer);
    let b = wordIndexOf(range.endContainer);
    // A selection that ends right at the start of a word does not include it.
    if (b !== null && a !== null && b > a && range.endOffset === 0) b -= 1;
    if (a === null || b === null) return;
    setSelection({ from: Math.min(a, b), to: Math.max(a, b) });
    current.removeAllRanges();
  };

  // Stable identity, so the memoized rows do not re-render on every tick of the player.
  const onWordRef = useRef<(index: number) => void>(() => undefined);
  onWordRef.current = (index: number) => {
    const current = window.getSelection?.();
    if (current && !current.isCollapsed) return;
    setSelection(null);
    onSeek((words[index] as Word).start);
  };
  const onWord = useCallback((index: number) => onWordRef.current(index), []);

  if (words.length === 0) {
    return (
      <div className={styles.none}>
        <EmptyState bare icon="transcript" title="Este video no tiene transcripción" description="Sin texto no se pueden marcar clips por palabras. Usá el reproductor y la línea de tiempo." />
      </div>
    );
  }

  const selStart = selection ? (words[selection.from] as Word).start : 0;
  const selEnd = selection ? (words[selection.to] as Word).end : 0;
  const selRow = selection ? rows.find((row) => row.to >= selection.to)?.index : undefined;
  const firstInRow = inRange ? rows.find((row) => row.to >= inRange.from)?.index : undefined;
  const lastInRow = inRange ? rows.find((row) => row.to >= inRange.to)?.index : undefined;

  const moves: Array<{ key: string; label: string; time: number; run: () => void }> = [];
  if (selection && clip && canEdit) {
    if (selEnd > clip.end + 0.05) moves.push({ key: 'end', label: `Llevar el fin del clip ${clip.n} acá`, time: selEnd, run: () => onMoveEdge('end', selEnd) });
    if (selStart < clip.start - 0.05) moves.push({ key: 'start', label: `Llevar el inicio del clip ${clip.n} acá`, time: selStart, run: () => onMoveEdge('start', selStart) });
    if (moves.length === 0 && (selStart > clip.start + 0.05 || selEnd < clip.end - 0.05)) {
      moves.push({ key: 'fit', label: `Ajustar el clip ${clip.n} a la selección`, time: selStart, run: () => onFitClip(selStart, selEnd) });
    }
  }
  const done = (run: () => void) => () => {
    run();
    setSelection(null);
  };

  const selectionActions = selection ? (
    <div className={cx(styles.selPop, mobile && styles.selBar, variant === 'main' && styles.selMain)} role="group" aria-label="Acciones para la selección">
      <div className={styles.selMeta}>
        <span>Selección</span>
        <span>
          <span className="timecode">
            {formatTimecode(selStart)}–{formatTimecode(selEnd)}
          </span>{' '}
          · <span className="num">{formatDuration(selEnd - selStart)}</span>
        </span>
      </div>
      {canEdit ? (
        <>
          {mobile || variant === 'main' ? (
            <Button
              variant={variant === 'main' ? 'primary' : 'secondary'}
              size={mobile ? 'md' : 'sm'}
              iconLeft="plus"
              className={styles.selBtn}
              onClick={done(() => onCreateClip(selStart, selEnd, textOf(words, selection.from, selection.to)))}
            >
              Crear clip con la selección
            </Button>
          ) : (
            <button type="button" className={styles.menuItem} onClick={done(() => onCreateClip(selStart, selEnd, textOf(words, selection.from, selection.to)))}>
              <Icon name="plus" size={14} />
              Crear clip con la selección
            </button>
          )}
          {moves.map((move) =>
            mobile ? (
              <Button key={move.key} className={styles.selBtn} iconLeft="clips" onClick={done(move.run)}>
                {move.label}
                <span className={cx('timecode', styles.k)}>{formatTimecode(move.time)}</span>
              </Button>
            ) : (
              <button key={move.key} type="button" className={styles.menuItem} onClick={done(move.run)}>
                <Icon name="clips" size={14} />
                {move.label}
                <span className={cx('timecode', styles.k)}>{formatTimecode(move.time)}</span>
              </button>
            ),
          )}
          {variant === 'main' ? <p className={styles.hint}>Los bordes se ajustan a palabras completas. Después podés afinarlos.</p> : null}
        </>
      ) : (
        <p className={styles.hint}>Pedile acceso a un admin para crear clips.</p>
      )}
      <button type="button" className={styles.menuItem} onClick={() => setSelection(null)}>
        <Icon name="close" size={14} />
        Quitar la selección
      </button>
    </div>
  ) : null;

  const search = (
    <div className={styles.search} role="search">
      <Input
        type="search"
        icon="search"
        value={query}
        placeholder="Buscar en la transcripción"
        aria-label="Buscar en la transcripción"
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') goToHit(event.shiftKey ? -1 : 1);
          if (event.key === 'Escape' && variant === 'side') {
            setQuery('');
            setSearching(false);
          }
        }}
      />
      {query.trim().length >= 2 ? (
        <span className={cx('num', styles.hits)} role="status">
          {matches.length === 0 ? 'Sin resultados' : `${hit + 1} de ${matches.length}`}
        </span>
      ) : null}
      {matches.length > 1 ? (
        <>
          <IconButton icon="chevron-up" size="sm" label="Resultado anterior" onClick={() => goToHit(-1)} />
          <IconButton icon="chevron-down" size="sm" label="Resultado siguiente" onClick={() => goToHit(1)} />
        </>
      ) : null}
    </div>
  );

  return (
    <>
      <div className={cx(styles.head, variant === 'main' && styles.headMain)}>
        <div className={styles.headTitle}>
          <h2 className="t-body">Transcripción</h2>
          {variant === 'main' ? <span className="t-small muted">Seleccioná texto para crear un clip</span> : null}
        </div>
        {variant === 'main' ? (
          search
        ) : (
          <IconButton
            icon={searching ? 'close' : 'search'}
            label={searching ? 'Cerrar la búsqueda' : 'Buscar en la transcripción'}
            aria-expanded={searching}
            onClick={() => {
              setSearching(!searching);
              setQuery('');
            }}
          />
        )}
      </div>
      {variant === 'side' && searching ? <div className={styles.searchRow}>{search}</div> : null}
      {mobile ? <p className={cx('t-small muted', styles.mobileHint)}>Mantené apretada una palabra y arrastrá para seleccionar. Los bordes se ajustan a palabras completas.</p> : null}
      <div
        ref={scroller}
        className={cx(styles.scroll, variant === 'main' && styles.scrollMain, mobile && styles.scrollMobile, searching && variant === 'side' && styles.scrollSearch)}
        onMouseUp={() => window.setTimeout(readSelection, 0)}
        onTouchEnd={() => window.setTimeout(readSelection, 0)}
        onKeyUp={(event) => {
          if (event.shiftKey || event.key === 'Shift') readSelection();
        }}
      >
        {rows.map((row) => {
          const active = activeWord >= row.from && activeWord <= row.to ? activeWord : -1;
          const selFrom = selection && selection.to >= row.from && selection.from <= row.to ? selection.from : -1;
          const inFrom = inRange && inRange.to >= row.from && inRange.from <= row.to ? inRange.from : -1;
          return (
            <Fragment key={row.index}>
              {clip && firstInRow === row.index ? (
                <div className={cx('timecode', styles.edge)}>
                  Inicio del clip {clip.n} · {formatTimecode(clip.start)}
                </div>
              ) : null}
              <RowView
                row={row}
                words={words}
                active={active}
                selFrom={selFrom}
                selTo={selFrom === -1 ? -1 : (selection?.to ?? -1)}
                inFrom={inFrom}
                inTo={inFrom === -1 ? -1 : (inRange?.to ?? -1)}
                found={matches.includes(row.index)}
                current={matches[hit] === row.index}
                onWord={onWord}
              />
              {clip && lastInRow === row.index ? (
                <div className={cx('timecode', styles.edge)}>
                  Fin del clip {clip.n} · {formatTimecode(clip.end)}
                </div>
              ) : null}
              {selRow === row.index ? selectionActions : null}
            </Fragment>
          );
        })}
      </div>
    </>
  );
}
