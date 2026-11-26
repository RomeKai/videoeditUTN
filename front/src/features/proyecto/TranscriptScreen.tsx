'use client';

import Link from 'next/link';
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Clip, Project } from '@/api/contracts/projects';
import { downloadText, transcriptToSrt, transcriptToTxt, type TranscriptLine } from '@/lib/download';
import { formatDuration, formatNumber, formatTimecode } from '@/lib/format';
import { useDebounced } from '@/lib/hooks';
import { Badge, Button, Card, EmptyState, Icon, IconButton, Input, Kbd, NetworkChip, Timecode, VideoThumb, useToast } from '@/ui';
import {
  clipDuration,
  describeClip,
  findMatches,
  groupLinesByClip,
  isWholeVideo,
  projectDuration,
  rulerTicks,
  shortDateTime,
  transcriptLines,
  wordCount,
  type TranscriptMatch,
} from './model';
import { useCurrentProject } from './project-context';
import styles from './TranscriptScreen.module.css';

/** Lines rendered at first and added by "Mostrar más": a one-hour video has about a thousand. */
const PAGE = 150;

function slug(title: string): string {
  return title.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').toLowerCase() || 'transcripcion';
}

function Highlighted({ text, matches, currentIndex, offset }: { text: string; matches: TranscriptMatch[]; currentIndex: number; offset: number }) {
  if (matches.length === 0) return <>{text}</>;
  const parts: ReactNode[] = [];
  let cursor = 0;
  matches.forEach((match, index) => {
    if (match.start > cursor) parts.push(<Fragment key={`t${index}`}>{text.slice(cursor, match.start)}</Fragment>);
    const current = offset + index === currentIndex;
    parts.push(
      <mark key={`m${index}`} className={current ? `${styles.hit} ${styles.hitCurrent}` : styles.hit} data-current={current || undefined}>
        {text.slice(match.start, match.end)}
      </mark>,
    );
    cursor = match.end;
  });
  if (cursor < text.length) parts.push(<Fragment key="tail">{text.slice(cursor)}</Fragment>);
  return <>{parts}</>;
}

function ClipNote({ clip, href, projectStatus }: { clip: Clip; href: string; projectStatus: Project['status'] }) {
  const display = describeClip(clip, projectStatus);
  return (
    <aside className={styles.note} aria-label="Clip de este tramo">
      <VideoThumb ratio="9:16" xs width={40} seed={clip.id} />
      <div className={styles.noteBody}>
        <span className="t-over">Clip</span>
        <Link className={styles.noteTitle} href={href}>
          {clip.title}
        </Link>
        <span className={styles.noteMeta}>
          <span className="timecode">
            {formatTimecode(clip.start_time)}–{formatTimecode(clip.end_time)}
          </span>
          <span className="num">{formatDuration(clipDuration(clip))}</span>
          {clip.virality_score > 0 ? (
            <span className={`${styles.score} num`} aria-label={`Puntaje ${clip.virality_score} de 100`}>
              <span className={styles.scoreDot} aria-hidden="true" />
              {clip.virality_score}
            </span>
          ) : null}
        </span>
        <span className={styles.noteMeta}>
          <Badge tone={display.tone} size="sm" dot={!display.icon} icon={display.icon}>
            {display.label}
          </Badge>
          {display.where ? (
            <>
              <NetworkChip platform={display.where.platform} size="sm" short />
              <span className="num">{shortDateTime(display.where.publishAt)}</span>
            </>
          ) : null}
        </span>
        <Link className={`${styles.arrowLink} t-small`} href={href}>
          {href.endsWith('/resultado') ? 'Ver resultado' : 'Ver clip'}
          <Icon name="chevron-right" size={14} />
        </Link>
      </div>
    </aside>
  );
}

function ClipMap({ project, lines, duration, visibleRange }: { project: Project; lines: TranscriptLine[]; duration: number; visibleRange: [number, number] | null }) {
  const ticks = rulerTicks(duration);
  const percent = (seconds: number) => `${Math.min(100, Math.max(0, (seconds / duration) * 100)).toFixed(2)}%`;
  const from = visibleRange ? lines[visibleRange[0]] : undefined;
  const to = visibleRange ? lines[visibleRange[1]] : undefined;
  return (
    <div className={styles.map}>
      <div className={styles.mapHead}>
        <h2 className={styles.mapTitle}>Dónde están los clips</h2>
        <div className={styles.legend}>
          <span>
            <i className={`${styles.swatch} ${styles.swatchClip}`} />
            Tramo que se volvió clip
          </span>
          <span>
            <i className={`${styles.swatch} ${styles.swatchWindow}`} />
            Lo que estás leyendo
          </span>
        </div>
      </div>
      <div className={styles.ruler} aria-hidden="true">
        {ticks.map((tick, index) => (
          <span
            key={tick}
            className={`${styles.tick} ${index === 0 ? styles.tickFirst : index === ticks.length - 1 ? styles.tickLast : ''}`}
            style={index === 0 ? undefined : { left: percent(tick) }}
          >
            {formatTimecode(tick)}
          </span>
        ))}
      </div>
      <div className={styles.overview}>
        {project.clips.map((clip) => (
          <a
            key={clip.id}
            className={styles.marker}
            href={`#clip-${clip.id}`}
            style={{ left: percent(clip.start_time), width: percent(clipDuration(clip)) }}
            title={`${clip.title} · ${formatTimecode(clip.start_time)}–${formatTimecode(clip.end_time)}`}
            aria-label={`Clip: ${clip.title}, ${formatTimecode(clip.start_time)}–${formatTimecode(clip.end_time)}`}
          />
        ))}
        {from && to ? <span className={styles.window} style={{ left: percent(from.start), width: percent(Math.max(1, to.end - from.start)) }} aria-hidden="true" /> : null}
      </div>
    </div>
  );
}

/** D1e · Transcripción: the whole text with timecodes, search, the stretches that became clips, copy and .txt/.srt download. */
export function TranscriptScreen({ projectId }: { projectId: string }) {
  const project = useCurrentProject();
  const toast = useToast();
  const [query, setQuery] = useState('');
  const [current, setCurrent] = useState(0);
  const [limit, setLimit] = useState(PAGE);
  const [visibleRange, setVisibleRange] = useState<[number, number] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const debounced = useDebounced(query, 200);

  const lines = useMemo(() => (project ? transcriptLines(project) : []), [project]);
  const groups = useMemo(() => groupLinesByClip(lines, project?.clips ?? []), [lines, project?.clips]);
  const matches = useMemo(() => findMatches(lines, debounced), [lines, debounced]);
  const searching = debounced.trim().length >= 2;
  const matchesByLine = useMemo(() => {
    const map = new Map<number, { items: TranscriptMatch[]; offset: number }>();
    matches.forEach((match, index) => {
      const entry = map.get(match.line);
      if (entry) entry.items.push(match);
      else map.set(match.line, { items: [match], offset: index });
    });
    return map;
  }, [matches]);

  useEffect(() => setCurrent(0), [debounced]);

  // "/" focuses the search, as the hint in the box says.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
      event.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // The current match is always rendered and in view.
  const currentMatch = matches[current];
  useEffect(() => {
    if (!currentMatch) return;
    if (currentMatch.line >= limit) setLimit(Math.ceil((currentMatch.line + 1) / PAGE) * PAGE);
    const mark = listRef.current?.querySelector<HTMLElement>('mark[data-current]');
    mark?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, [currentMatch, limit]);

  // "Lo que estás leyendo": the lines on screen, drawn as a window on the map.
  useEffect(() => {
    const root = listRef.current;
    if (!root || typeof IntersectionObserver === 'undefined') return;
    const seen = new Set<number>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const index = Number((entry.target as HTMLElement).dataset.line);
        if (entry.isIntersecting) seen.add(index);
        else seen.delete(index);
      }
      setVisibleRange(seen.size > 0 ? [Math.min(...seen), Math.max(...seen)] : null);
    });
    root.querySelectorAll('[data-line]').forEach((row) => observer.observe(row));
    return () => observer.disconnect();
  }, [lines, limit]);

  if (!project) return null;
  const base = `/proyectos/${projectId}`;

  if (lines.length === 0) {
    if (project.status === 'failed') {
      return (
        <EmptyState
          icon="transcript"
          title="Este proyecto no llegó a transcribirse"
          description="El proceso falló antes de este paso."
          action={
            <Button variant="secondary" href={base}>
              Ver qué pasó
            </Button>
          }
        />
      );
    }
    if (project.status === 'uploaded' || project.status === 'ingesting') {
      return (
        <EmptyState
          icon="transcript"
          title="Todavía no hay transcripción"
          description="Aparece acá cuando terminemos de pasar a texto lo que se dice en el video."
          action={
            <Button variant="secondary" href={base}>
              Ver el proceso
            </Button>
          }
        />
      );
    }
    return <EmptyState icon="transcript" title="Este video no tiene voz" description="No encontramos nada para transcribir. Si es un video completo, se editó igual, sin subtítulos." />;
  }

  const duration = projectDuration(project) ?? lines[lines.length - 1]?.end ?? 0;
  const words = wordCount(project);
  const shown = Math.min(limit, lines.length);
  const step = (delta: number) => {
    if (matches.length === 0) return;
    setCurrent((value) => (value + delta + matches.length) % matches.length);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(transcriptToTxt(project.transcript_data ?? []));
      toast.success('Transcripción copiada');
    } catch {
      toast.error('No pudimos copiar la transcripción.');
    }
  };

  const renderRow = (index: number) => {
    const line = lines[index];
    if (!line) return null;
    const found = matchesByLine.get(index);
    return (
      <div key={index} className={styles.row} data-line={index}>
        <Timecode seconds={line.start} />
        <p>{found ? <Highlighted text={line.text} matches={found.items} offset={found.offset} currentIndex={current} /> : line.text}</p>
      </div>
    );
  };

  return (
    <>
      <div className={styles.toolbar}>
        <div className={styles.find}>
          <div className={styles.search} role="search">
            <Input
              ref={inputRef}
              type="search"
              icon="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  step(event.shiftKey ? -1 : 1);
                } else if (event.key === 'Escape' && query) {
                  event.preventDefault();
                  setQuery('');
                }
              }}
              placeholder="Buscar en la transcripción"
              aria-label="Buscar en la transcripción"
              autoComplete="off"
            />
            {searching ? (
              <span className={`${styles.hits} num`} role="status">
                {matches.length > 0 ? `${current + 1} de ${matches.length}` : '0'}
              </span>
            ) : (
              <span className={styles.kbd} aria-hidden="true">
                <Kbd>/</Kbd>
              </span>
            )}
          </div>
          {searching && matches.length > 0 ? (
            <>
              <IconButton icon="chevron-up" label="Resultado anterior" onClick={() => step(-1)} />
              <IconButton icon="chevron-down" label="Resultado siguiente" onClick={() => step(1)} />
            </>
          ) : null}
          {searching && matches.length === 0 ? <IconButton icon="close" label="Borrar la búsqueda" onClick={() => setQuery('')} /> : null}
        </div>
        <span className="t-small muted num">
          {formatNumber(words, 0)} palabras · {formatDuration(duration)}
        </span>
        <span className={styles.spacer} />
        <div className={styles.tools}>
          <Button size="sm" variant="secondary" iconLeft="copy" onClick={() => void copy()}>
            Copiar
          </Button>
          <Button
            size="sm"
            variant="secondary"
            iconLeft="download"
            onClick={() => {
              downloadText(transcriptToTxt(project.transcript_data ?? []), `${slug(project.title)}.txt`);
              toast.success('Transcripción descargada');
            }}
          >
            Descargar .txt
          </Button>
          <Button
            size="sm"
            variant="secondary"
            iconLeft="captions"
            title="Subtítulos con marcas de tiempo"
            onClick={() => {
              downloadText(transcriptToSrt(project.transcript_data ?? []), `${slug(project.title)}.srt`, 'application/x-subrip;charset=utf-8');
              toast.success('Subtítulos descargados');
            }}
          >
            Descargar .srt
          </Button>
        </div>
      </div>
      {searching && matches.length === 0 ? (
        <div className={styles.emptyFind} role="status">
          <p className={styles.emptyFindTitle}>No encontramos «{debounced.trim()}» en este video.</p>
          <p className="t-small muted">Probá con otra palabra o revisá cómo está escrita.</p>
        </div>
      ) : null}
      <Card as="section" aria-label="Transcripción completa">
        {project.clips.length > 0 && duration > 0 ? <ClipMap project={project} lines={lines} duration={duration} visibleRange={visibleRange} /> : null}
        <div className={styles.text} ref={listRef}>
          {groups
            .filter((group) => group.from < shown)
            .map((group) => {
              const rows = [];
              for (let index = group.from; index <= Math.min(group.to, shown - 1); index += 1) rows.push(renderRow(index));
              const first = lines[group.from];
              const last = lines[group.to];
              return group.clip ? (
                <div key={`${group.from}-${group.clip.id}`} className={`${styles.group} ${styles.isClip}`} id={`clip-${group.clip.id}`}>
                  <div
                    className={styles.rows}
                    role="group"
                    aria-label={`Tramo del clip «${group.clip.title}», ${formatTimecode(first?.start ?? 0)}–${formatTimecode(last?.end ?? 0)}`}
                  >
                    {rows}
                  </div>
                  <ClipNote projectStatus={project.status} clip={group.clip} href={isWholeVideo(project) ? `${base}/resultado` : `${base}/clips`} />
                </div>
              ) : (
                <div key={group.from} className={styles.group}>
                  <div className={styles.rows}>{rows}</div>
                </div>
              );
            })}
          {shown < lines.length ? (
            <button type="button" className={styles.more} onClick={() => setLimit((value) => value + PAGE * 2)}>
              <span>
                <Icon name="chevron-down" size={14} />
                Mostrar lo que sigue ·{' '}
                <span className="timecode">
                  {formatTimecode(lines[shown]?.start ?? 0)}–{formatTimecode(duration)}
                </span>
              </span>
            </button>
          ) : null}
        </div>
      </Card>
    </>
  );
}
