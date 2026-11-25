'use client';

import { useId, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { useFieldControl } from './Field';
import styles from './FileDropZone.module.css';
import { Icon, type IconName } from './Icon';

export interface FileDropZoneProps {
  onFiles: (files: File[]) => void;
  /** Same syntax as <input accept>: ".mp4,.mov,.avi" or "video/*". */
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  invalid?: boolean;
  icon?: IconName;
  title?: ReactNode;
  description?: ReactNode;
  /** Name for the hidden file input (also its accessible name). */
  inputLabel?: string;
  className?: string;
  children?: ReactNode;
}

/** Dashed area to drop or pick a file (kit: .dropzone). The dashed border is only for real drop targets. */
export function FileDropZone({
  onFiles,
  accept,
  multiple = false,
  disabled = false,
  invalid,
  icon = 'upload',
  title = 'Arrastrá tu archivo acá',
  description = 'o elegilo desde tu computadora',
  inputLabel = 'Elegir archivo',
  className,
  children,
}: FileDropZoneProps) {
  const field = useFieldControl();
  const generated = useId();
  const inputId = field?.id ?? generated;
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const isInvalid = invalid ?? field?.invalid ?? false;

  const take = (list: FileList | null) => {
    if (!list || list.length === 0) return;
    const files = Array.from(list);
    onFiles(multiple ? files : files.slice(0, 1));
  };

  const onDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setOver(false);
    if (!disabled) take(event.dataTransfer.files);
  };

  return (
    <label
      htmlFor={inputId}
      className={cx(styles.zone, over && styles.over, isInvalid && styles.invalid, disabled && styles.disabled, className)}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        className={styles.native}
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        aria-label={inputLabel}
        aria-describedby={field?.describedBy}
        aria-invalid={isInvalid || undefined}
        onChange={(event) => {
          take(event.target.files);
          // Picking the same file again must fire change.
          event.target.value = '';
        }}
      />
      {children ?? (
        <>
          <span className={styles.icon}>
            <Icon name={icon} size={20} />
          </span>
          <span className={styles.title}>{title}</span>
          {description ? <span className="t-small muted">{description}</span> : null}
        </>
      )}
    </label>
  );
}
