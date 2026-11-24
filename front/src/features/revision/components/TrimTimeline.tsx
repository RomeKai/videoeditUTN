'use client';

import { useMemo, useRef, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from 'react';
import { cx } from '@/lib/cx';
import { formatTimecode } from '@/lib/format';
import { rng } from '../lib/random';
import { clamp, formatEdge, rulerTicks } from '../lib/time';
import { lastWordStartingBefore, type Word } from '../lib/transcript';
import styles from './timeline.module.css';

export interface TimeWindow {
  from: number;
  to: number;
}

/** The stretch of video the detail lanes show around a clip: the clip plus some air on each side. */
export function windowAround(start: number, end: number, total: number): TimeWindow {
  const pad = clamp((end - start) * 0.5, 6, 30);
  const from = Math.max(0, Math.floor(start - pad));
  const to = Math.min(Math.max(total, end), Math.ceil(end + pad));
  return { from, to: Math.max(to, from + 1) };
}

export interface TrimTimelineProps {
  words: ReadonlyArray<Word>;
  window: TimeWindow;
  start: number;
  end: number;
  /** D4: the range of the render that exists, drawn as an outline; what the new trim adds is hatched. */
  rendered?: { start: number; end: number };
  /** Playhead, in seconds of the source video. Null hides it. */
  time: number | null;
  /** Seed of the drawn waveform (project id). */
  seed: string;
  disabled?: boolean;
  tall?: boolean;
  onSeek?: (seconds: number) => void;
  /** Dragging a handle: the raw time under the pointer. The screen snaps it to a word. */
  onDrag: (edge: 'start' | 'end', seconds: number) => void;
  /** Keyboard on a handle: one word back or forward. */
  onStep: (edge: 'start' | 'end', direction: -1 | 1) => void;
}

const BARS = 160;

/**
 * Detail of the timeline: an audio lane, a words lane, the clip range with two
 * handles and the playhead. Handles are sliders: drag them, or focus one and
 * use ← →.
 */
export function TrimTimeline({ words, window: view, start, end, rendered, time, seed, disabled, tall, onSeek, onDrag, onStep }: TrimTimelineProps) {
  const lanes = useRef<HTMLDivElement>(null);
  const span = view.to - view.from;
  const pct = (seconds: number) => `${clamp(((seconds - view.from) / span) * 100, 0, 100)}%`;
  const width = (from: number, to: number) => `${clamp(((to - from) / span) * 100, 0, 100)}%`;

  // The mock has no audio: a stable drawing per project and window stands in for the waveform.
  const bars = useMemo(() => {
    let hash = 0;
    for (const char of `${seed}:${view.from}`) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
    const random = rng(hash);
    return Array.from({ length: BARS }, (_, index) => {
      const at = view.from + ((index + 0.5) / BARS) * span;
      const word = lastWordStartingBefore(words, at);
      const speaking = word >= 0 && at <= (words[word] as Word).end + 0.15;
      return (speaking || words.length === 0 ? 0.35 + random() * 0.6 : 0.04 + random() * 0.1) * 100;
    });
  }, [seed, span, view.from, words]);

  const visibleWords = useMemo(() => {
    const first = Math.max(0, lastWordStartingBefore(words, view.from));
    const out: Word[] = [];
    for (let index = first; index < words.length; index += 1) {
      const word = words[index] as Word;
      if (word.start > view.to) break;
      if (word.end >= view.from) out.push(word);
    }
    return out;
  }, [view.from, view.to, words]);

  const timeAt = (clientX: number): number => {
    const box = lanes.current?.getBoundingClientRect();
    if (!box || box.width === 0) return view.from;
    return view.from + clamp((clientX - box.left) / box.width, 0, 1) * span;
  };

  const startDrag = (edge: 'start' | 'end') => (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (disabled) return;
    event.preventDefault();
    event.stopPropagation();
    const handle = event.currentTarget;
    handle.setPointerCapture?.(event.pointerId);
    const move = (moveEvent: PointerEvent) => onDrag(edge, timeAt(moveEvent.clientX));
    const stop = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', stop);
      handle.removeEventListener('pointercancel', stop);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  };

  const onHandleKey = (edge: 'start' | 'end') => (event: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
    event.preventDefault();
    event.stopPropagation();
    onStep(edge, event.key === 'ArrowLeft' ? -1 : 1);
  };

  const ticks = rulerTicks(view.from, view.to);
  const added =
    rendered && (start < rendered.start || end > rendered.end)
      ? { head: Math.max(0, rendered.start - start), tail: Math.max(0, end - rendered.end) }
      : null;

  return (
    <div className={styles.detail}>
      <div
        ref={lanes}
        className={styles.lanes}
        onPointerDown={(event) => {
          if (event.button === 0) onSeek?.(timeAt(event.clientX));
        }}
      >
        <div className={cx(styles.lane, styles.audio, tall && styles.tall)} aria-hidden="true">
          <svg className={styles.wave} viewBox={`0 0 ${BARS} 100`} preserveAspectRatio="none">
            {bars.map((height, index) => (
              <rect key={index} x={index + 0.2} y={50 - height / 2} width={0.6} height={height} fill="currentColor" />
            ))}
          </svg>
          {rendered ? <span className={styles.curOut} style={{ left: pct(rendered.start), width: width(rendered.start, rendered.end) }} /> : null}
        </div>
        <div className={cx(styles.lane, styles.words)} aria-hidden="true">
          {visibleWords.map((word) => (
            <span key={word.start} className={styles.wordTick} style={{ left: pct(word.start), width: width(word.start, word.end) }} />
          ))}
        </div>
        <div className={styles.range} style={{ left: pct(start), width: width(Math.max(start, view.from), Math.min(end, view.to)) }}>
          {added && added.head > 0 ? <span className={cx(styles.ghost, styles.hatch)} style={{ left: 0, width: `${(added.head / (end - start)) * 100}%` }} /> : null}
          {added && added.tail > 0 ? <span className={cx(styles.ghost, styles.hatch)} style={{ right: 0, width: `${(added.tail / (end - start)) * 100}%` }} /> : null}
          <span className={cx('timecode', styles.flag, styles.flagStart)}>{formatEdge(start)}</span>
          <span className={cx('timecode', styles.flag, styles.flagEnd)}>{formatEdge(end)}</span>
          <button
            type="button"
            role="slider"
            className={cx(styles.handle, styles.handleStart)}
            aria-label="Inicio del clip"
            aria-valuemin={Math.round(view.from)}
            aria-valuemax={Math.round(end)}
            aria-valuenow={Math.round(start)}
            aria-valuetext={formatEdge(start)}
            aria-orientation="horizontal"
            aria-disabled={disabled || undefined}
            onPointerDown={startDrag('start')}
            onKeyDown={onHandleKey('start')}
          />
          <button
            type="button"
            role="slider"
            className={cx(styles.handle, styles.handleEnd)}
            aria-label="Fin del clip"
            aria-valuemin={Math.round(start)}
            aria-valuemax={Math.round(view.to)}
            aria-valuenow={Math.round(end)}
            aria-valuetext={formatEdge(end)}
            aria-orientation="horizontal"
            aria-disabled={disabled || undefined}
            onPointerDown={startDrag('end')}
            onKeyDown={onHandleKey('end')}
          />
        </div>
        {time !== null && time >= view.from && time <= view.to ? <span className={styles.playhead} style={{ left: pct(time) }} /> : null}
      </div>
      <div className={styles.ruler} aria-hidden="true">
        {ticks.map((tick, index) => (
          <span
            key={tick}
            className={cx(styles.tick, index === 0 && tick === view.from && styles.tickFirst, tick === view.to && styles.tickLast)}
            style={{ left: pct(tick) }}
          >
            {formatTimecode(tick)}
          </span>
        ))}
      </div>
    </div>
  );
}
