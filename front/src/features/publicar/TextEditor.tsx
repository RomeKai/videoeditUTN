'use client';

import { useId, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react';
import type { Platform } from '@/api/contracts/common';
import type { SocialAccount } from '@/api/contracts/social';
import { formatNumber } from '@/lib/format';
import { Button, Field, Icon, Num, Skeleton, Spinner, Textarea, cx } from '@/ui';
import styles from './Publish.module.css';
import { NETWORK_NOUN, parseHashtags, textLength } from './posts';
import type { TextValue } from './publish';

export interface HashtagInputProps {
  value: string[];
  onChange: (value: string[]) => void;
  labelledBy: string;
  invalid?: boolean;
  disabled?: boolean;
}

/** Hashtags as chips: Enter, space or comma adds one; Backspace on the empty box removes the last. */
export function HashtagInput({ value, onChange, labelledBy, invalid, disabled }: HashtagInputProps) {
  const [text, setText] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const commit = (raw: string) => {
    const added = parseHashtags(raw);
    setText('');
    if (added.length === 0) return;
    onChange(parseHashtags([...value, ...added].join(' ')));
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ' ' || event.key === ',') {
      if (text.trim()) {
        event.preventDefault();
        commit(text);
      } else if (event.key !== 'Enter') event.preventDefault();
    } else if (event.key === 'Backspace' && !text && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };
  const onPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const pasted = event.clipboardData.getData('text');
    if (!/[\s,#]/.test(pasted)) return;
    event.preventDefault();
    commit(`${text} ${pasted}`);
  };
  return (
    // The box behaves like one text field: clicking anywhere in it puts the cursor in the input.
    <div className={cx(styles.tags, invalid && styles.tagsInvalid)} role="group" aria-labelledby={labelledBy} onClick={() => input.current?.focus()}>
      {value.map((tag) => (
        <span key={tag} className={styles.tag}>
          #{tag}
          <button
            type="button"
            className={styles.tagRemove}
            aria-label={`Quitar #${tag}`}
            disabled={disabled}
            onClick={(event) => {
              event.stopPropagation();
              onChange(value.filter((item) => item !== tag));
            }}
          >
            <Icon name="close" size={10} />
          </button>
        </span>
      ))}
      <input
        ref={input}
        className={styles.tagInput}
        value={text}
        disabled={disabled}
        aria-label="Agregar hashtag"
        placeholder={value.length === 0 ? '#hashtag' : ''}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onBlur={() => {
          if (text.trim()) commit(text);
        }}
      />
    </div>
  );
}

export type TextOrigin = 'ai' | 'edited' | 'manual';

export interface TextEditorProps {
  platform: Platform;
  account: SocialAccount | undefined;
  value: TextValue;
  onChange: (value: TextValue) => void;
  origin: TextOrigin;
  /** The AI text of this publication is on its way. */
  generating: boolean;
  /** Absent: the project has no AI text, or there is nothing to regenerate yet. */
  onRegenerate?: () => void;
  /** Id of the caption textarea, so the footer's reason can take the user there. */
  captionId?: string;
  /** For several clips the label is the clip's title. */
  label?: string;
  disabled?: boolean;
}

/** Caption and hashtags of one publication, with the network's limits (map E1.2). */
export function TextEditor({ platform, account, value, onChange, origin, generating, onRegenerate, captionId, label = 'Caption', disabled }: TextEditorProps) {
  const tagsLabelId = useId();
  const errorId = useId();
  const limit = account?.limits.caption_max_chars ?? 2200;
  const maxTags = account?.limits.hashtags_max ?? 30;
  const length = textLength(value.caption, value.hashtags);
  const over = length - limit;
  const tooManyTags = value.hashtags.length > maxTags;
  const noun = NETWORK_NOUN[platform];

  const aside = (
    <span className={styles.aside}>
      {generating ? (
        <span className={styles.genLabel} role="status">
          <Spinner size={12} />
          Generando texto…
        </span>
      ) : origin === 'ai' ? (
        <span className={styles.ai}>
          <Icon name="sparkles" size={12} />
          Sugerido por IA
        </span>
      ) : origin === 'edited' ? (
        <span className={cx(styles.ai, styles.aiEdited)}>Editado por vos</span>
      ) : null}
      {onRegenerate && !generating ? (
        <Button variant="ghost" size="sm" iconLeft="retry" className={styles.regen} onClick={onRegenerate} disabled={disabled}>
          Regenerar
        </Button>
      ) : null}
    </span>
  );

  return (
    <div className={styles.textBlock}>
      <Field label={label} labelAside={aside} htmlFor={captionId}>
        {generating ? (
          <div className={styles.gen} aria-busy="true" aria-label="Generando texto">
            <Skeleton width="92%" height={8} />
            <Skeleton width="64%" height={8} />
            <span className={styles.hint}>Si publicás ahora, el texto se completa solo apenas esté listo.</span>
          </div>
        ) : (
          <Textarea
            id={captionId}
            className={cx(styles.cap, over > 0 && styles.capLong)}
            value={value.caption}
            invalid={over > 0}
            disabled={disabled}
            placeholder="Escribí el texto de la publicación"
            aria-describedby={over > 0 ? errorId : undefined}
            onChange={(event) => onChange({ ...value, caption: event.target.value })}
          />
        )}
      </Field>
      {generating ? null : (
        <div className={styles.textBlock} style={{ gap: 6 }}>
          <span className={styles.label} id={tagsLabelId}>
            Hashtags
          </span>
          <HashtagInput value={value.hashtags} onChange={(hashtags) => onChange({ ...value, hashtags })} labelledBy={tagsLabelId} invalid={tooManyTags} disabled={disabled} />
          <div className={styles.countRow}>
            <span className={cx(tooManyTags && styles.countErr)}>
              <Num>{value.hashtags.length}</Num> de <Num>{maxTags}</Num> hashtags. Cuentan para el límite de caracteres.
            </span>
            <span className={cx('num', over > 0 && styles.countErr)} aria-label={`${formatNumber(length)} de ${formatNumber(limit)} caracteres${over > 0 ? `: sobran ${formatNumber(over)}` : ''}`}>
              {formatNumber(length)} / {formatNumber(limit)}
            </span>
          </div>
          {over > 0 ? (
            <>
              <div className={styles.meter} aria-hidden="true">
                <span style={{ width: `${(limit / length) * 100}%` }} />
                <span className={styles.meterOver} />
              </div>
              <p className={styles.fieldError} id={errorId} role="alert">
                <Icon name="error" size={14} />
                <span>
                  {over === 1 ? 'Sobra' : 'Sobran'} <Num>{formatNumber(over)}</Num> {over === 1 ? 'carácter' : 'caracteres'}. {noun} no acepta textos de más de <Num>{formatNumber(limit)}</Num>, contando los hashtags.{' '}
                  {onRegenerate ? 'Acortá el texto o usá «Regenerar» para volver a una versión corta.' : 'Acortá el texto.'}
                </span>
              </p>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
