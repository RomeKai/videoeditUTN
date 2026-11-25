'use client';

import { useState } from 'react';
import { NO_ACCESS_REASON } from '@/lib/permissions';
import { Input } from '@/ui';
import { formatEdge, parseEdge } from '../lib/time';
import styles from './timeline.module.css';

export interface EdgeInputProps {
  /** Seconds in the source video. */
  value: number;
  readOnly?: boolean;
  /** Why it cannot be edited (tooltip). Defaults to the role message. */
  readOnlyReason?: string;
  id?: string;
  className?: string;
  /** Called with the typed time (Enter or leaving the field). The screen snaps it to a word. */
  onCommit: (seconds: number) => void;
}

/** Time field of a clip border ("04:12,0"). What is not a time goes back to the current value. */
export function EdgeInput({ value, readOnly, readOnlyReason = NO_ACCESS_REASON, id, className, onCommit }: EdgeInputProps) {
  // What is being typed, or null when the field just shows the value. The shown text is derived from
  // the value in the same render: copying it into state from an effect left the field one render
  // behind, so right after a ±0,5 s click it still read the old time.
  const [typed, setTyped] = useState<string | null>(null);
  const text = typed ?? formatEdge(value);
  const invalid = typed !== null && parseEdge(typed) === null;
  const commit = () => {
    if (typed === null) return;
    const parsed = parseEdge(typed);
    // Back to showing the value: the new one when the screen accepts the time (snapped to a word),
    // the current one when what was typed is not a time or lands on the same word.
    setTyped(null);
    if (parsed !== null && Math.abs(parsed - value) > 0.04) onCommit(parsed);
  };
  return (
    <Input
      id={id}
      className={[styles.timeIn, className].filter(Boolean).join(' ')}
      value={text}
      inputMode="decimal"
      autoComplete="off"
      spellCheck={false}
      readOnly={readOnly}
      title={readOnly ? readOnlyReason : undefined}
      invalid={invalid}
      onChange={(event) => setTyped(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          commit();
        } else if (event.key === 'Escape') {
          setTyped(null);
        }
      }}
    />
  );
}
