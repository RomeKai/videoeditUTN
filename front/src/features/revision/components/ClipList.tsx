'use client';

import { useState } from 'react';
import { cx } from '@/lib/cx';
import { formatDuration, formatTimecode } from '@/lib/format';
import { NO_ACCESS_REASON } from '@/lib/permissions';
import { Badge, Button, Icon, IconButton, Input, Score, VideoThumb } from '@/ui';
import type { DraftClip } from '../lib/draft';
import { explainReasoning } from '../lib/reasoning';
import { formatEdge } from '../lib/time';
import styles from './cliplist.module.css';

export interface ClipListProps {
  clips: ReadonlyArray<DraftClip>;
  selectedKey: string | null;
  canEdit: boolean;
  captionColor?: string;
  onSelect: (key: string, options?: { play?: boolean }) => void;
  onToggle: (key: string, included: boolean) => void;
  onRemove: (key: string) => void;
  /** Phone (M3): the selected clip opens its detail in the row, with the trim by ±0,5 s buttons. */
  mobile?: boolean;
  onRename?: (key: string, title: string) => void;
  onNudge?: (key: string, edge: 'start' | 'end', delta: number) => void;
}

function ScoreLine({ clip }: { clip: DraftClip }) {
  if (clip.score === null) {
    return (
      <Badge size="sm" tone="outline">
        Tuyo
      </Badge>
    );
  }
  return <Score value={clip.score} className={styles.score} />;
}

function Why({ reasoning }: { reasoning: string }) {
  const { chips, sentence } = explainReasoning(reasoning);
  return (
    <div className={styles.whyBox}>
      <p className="t-over">Por qué lo eligió la IA</p>
      {chips.length > 0 ? (
        <ul className={styles.whyChips} aria-label="Motivos">
          {chips.map((chip) => (
            <li key={chip}>
              <Badge size="sm" icon="check">
                {chip}
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
      <p className={styles.whyText}>{sentence}</p>
    </div>
  );
}

/** Left column of the review: the suggested clips with score, times, reasoning and the include tick. */
export function ClipList({ clips, selectedKey, canEdit, captionColor, onSelect, onToggle, onRemove, mobile = false, onRename, onNudge }: ClipListProps) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [panel, setPanel] = useState<'trim' | 'rename' | null>(null);

  if (mobile) {
    return (
      <ul className={styles.mList}>
        {clips.map((clip) => {
          const selected = clip.key === selectedKey;
          return (
            <li key={clip.key} className={cx(styles.mClip, selected && styles.mSel, !clip.included && styles.off)}>
              <div className={styles.mRow}>
                <label className={styles.mTick} title={canEdit ? undefined : NO_ACCESS_REASON}>
                  <input
                    type="checkbox"
                    className={styles.cbx}
                    checked={clip.included}
                    disabled={!canEdit}
                    aria-label={`Incluir «${clip.title}» en el render`}
                    onChange={(event) => onToggle(clip.key, event.target.checked)}
                  />
                </label>
                <button type="button" className={styles.mPick} aria-pressed={selected} aria-label={`Ver «${clip.title}» en el reproductor`} onClick={() => onSelect(clip.key)}>
                  <VideoThumb ratio="9:16" xs seed={clip.key} captions={captionColor ?? true} width={27} />
                  <span className={styles.mText}>
                    <span className={styles.mTitle}>{clip.title}</span>
                    <span className={styles.mLine}>
                      <ScoreLine clip={clip} />
                      <span className="timecode">
                        {formatTimecode(clip.start)}–{formatTimecode(clip.end)}
                      </span>
                      <span className={cx('num', styles.mDur)}>{formatDuration(clip.end - clip.start)}</span>
                    </span>
                  </span>
                </button>
              </div>
              {selected ? (
                <div className={styles.mDetail}>
                  {clip.reasoning ? (
                    <p className={styles.whyText}>
                      <span className={styles.whyLead}>Por qué lo eligió la IA.</span> {explainReasoning(clip.reasoning).sentence}
                    </p>
                  ) : null}
                  <div className={styles.mButtons}>
                    <Button className={styles.mBtn} aria-expanded={panel === 'trim'} disabledReason={canEdit ? undefined : NO_ACCESS_REASON} onClick={() => setPanel(panel === 'trim' ? null : 'trim')}>
                      Ajustar bordes
                    </Button>
                    <Button className={styles.mBtn} aria-expanded={panel === 'rename'} disabledReason={canEdit ? undefined : NO_ACCESS_REASON} onClick={() => setPanel(panel === 'rename' ? null : 'rename')}>
                      Renombrar
                    </Button>
                    {clip.clipId === null && canEdit ? <IconButton icon="trash" label={`Quitar «${clip.title}»`} onClick={() => onRemove(clip.key)} /> : null}
                  </div>
                  {panel === 'trim' && canEdit ? (
                    <div className={styles.mTrim} role="group" aria-label="Bordes del clip">
                      {(['start', 'end'] as const).map((edge) => (
                        <div key={edge} className={styles.mTrimRow}>
                          <span className="t-small muted">{edge === 'start' ? 'Inicio' : 'Fin'}</span>
                          <Button className={styles.mBtn} aria-label={`Mover el ${edge === 'start' ? 'inicio' : 'fin'} medio segundo antes`} onClick={() => onNudge?.(clip.key, edge, -0.5)}>
                            −0,5 s
                          </Button>
                          <output className={cx('timecode', styles.mVal)}>{formatEdge(edge === 'start' ? clip.start : clip.end)}</output>
                          <Button className={styles.mBtn} aria-label={`Mover el ${edge === 'start' ? 'inicio' : 'fin'} medio segundo después`} onClick={() => onNudge?.(clip.key, edge, 0.5)}>
                            +0,5 s
                          </Button>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {panel === 'rename' && canEdit ? (
                    <Input className={styles.mInput} value={clip.title} maxLength={255} aria-label="Título del clip" onChange={(event) => onRename?.(clip.key, event.target.value)} />
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <ol className={styles.list}>
      {clips.map((clip) => {
        const selected = clip.key === selectedKey;
        const expanded = open[clip.key] ?? false;
        return (
          <li key={clip.key} className={cx(styles.clip, selected && styles.selected, !clip.included && styles.off)} aria-current={selected ? 'true' : undefined}>
            <input
              type="checkbox"
              className={styles.cbx}
              checked={clip.included}
              disabled={!canEdit}
              title={canEdit ? undefined : NO_ACCESS_REASON}
              aria-label={`Incluir «${clip.title}» en el render`}
              onChange={(event) => onToggle(clip.key, event.target.checked)}
            />
            <button type="button" className={styles.thumb} aria-label={`Reproducir «${clip.title}»`} onClick={() => onSelect(clip.key, { play: true })}>
              <VideoThumb ratio="9:16" xs seed={clip.key} width={32} />
              <span className={styles.play} aria-hidden="true">
                <Icon name="play" size={12} />
              </span>
            </button>
            <div className={styles.body}>
              <div className={styles.titleRow}>
                <button type="button" className={styles.title} onClick={() => onSelect(clip.key)}>
                  <span className={cx('num', styles.n)} aria-hidden="true">
                    {clip.n}
                  </span>
                  <span className={styles.tt}>{clip.title || 'Sin título'}</span>
                </button>
                {clip.clipId === null && canEdit ? <IconButton icon="trash" size="sm" label={`Quitar «${clip.title}»`} onClick={() => onRemove(clip.key)} /> : null}
              </div>
              <div className={styles.summary}>
                <ScoreLine clip={clip} />
                <span className={cx('timecode', styles.time)}>
                  {formatTimecode(clip.start)}–{formatTimecode(clip.end)}
                </span>
                <span className={cx('num', styles.dur)}>{formatDuration(clip.end - clip.start)}</span>
                {clip.reasoning ? (
                  <button type="button" className={styles.why} aria-expanded={expanded} onClick={() => setOpen({ ...open, [clip.key]: !expanded })}>
                    Por qué <Icon name={expanded ? 'chevron-up' : 'chevron-down'} size={14} />
                  </button>
                ) : null}
              </div>
              {expanded && clip.reasoning ? <Why reasoning={clip.reasoning} /> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
