'use client';

import { useId } from 'react';
import { Checkbox } from '@/ui';
import styles from './acceso.module.css';
import { TERMS_ERROR } from './lib';

// The legal pages are not in the screen map yet: the anchors are the ones of the artboards.
export const TERMS_URL = '#terminos';
export const PRIVACY_URL = '#privacidad';

/** "Acepto los Términos y la Política de privacidad", with the error under the text (artboards A2 and A5). */
export function TermsCheckbox({
  checked,
  onChange,
  invalid,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  invalid: boolean;
}) {
  const errorId = useId();
  return (
    <div className={styles.terms}>
      <Checkbox
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? errorId : undefined}
        label={
          <>
            Acepto los{' '}
            <a className="lnk" href={TERMS_URL}>
              Términos
            </a>{' '}
            y la{' '}
            <a className="lnk" href={PRIVACY_URL}>
              Política de privacidad
            </a>
            .
          </>
        }
      />
      {invalid ? (
        <span className={styles.termsError} id={errorId} role="alert">
          {TERMS_ERROR}
        </span>
      ) : null}
    </div>
  );
}
