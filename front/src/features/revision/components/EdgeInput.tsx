'use client';

import { useEffect, useState } from 'react';
import { NO_ACCESS_REASON } from '@/lib/permissions';
import { Input } from '@/ui';
import { formatEdge, parseEdge } from '../lib/time';
import styles from './timeline.module.css';

export interface EdgeInputProps {
  /** Seconds in the source video. */
  value: number;
  readOnly?: boolean;
  id?: string;
  className?: string;
  /** Called with the typed time (Enter or leaving the field). The screen snaps it to a word. */
  onCommit: (seconds: number) => void;
}

/** Time field of a clip border ("04:12,0"). What is not a time goes back to the current value. */
export function EdgeInput({ value, readOnly, id, className, onCommit }: EdgeInputProps) {
  const [text, setText] = useState(formatEdge(value));
  const [invalid, setInvalid] = useState(false);
  useEffect(() => {
    setText(formatEdge(value));
    setInvalid(false);
  }, [value]);
  const commit = () => {
    const parsed = parseEdge(text);
    if (parsed === null) {
      setText(formatEdge(value));
      setInvalid(false);
      return;
    }
    if (Math.abs(parsed - value) > 0.04) onCommit(parsed);
    // When the snapped time equals the current one the value does not change: show it again.
    setText(formatEdge(value));
    setInvalid(false);
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
      title={readOnly ? NO_ACCESS_REASON : undefined}
      invalid={invalid}
      onChange={(event) => {
        setText(event.target.value);
        setInvalid(parseEdge(event.target.value) === null);
      }}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          commit();
        } else if (event.key === 'Escape') {
          setText(formatEdge(value));
          setInvalid(false);
        }
      }}
    />
  );
}
