'use client';

import { useId } from 'react';
import { Checkbox } from '@/ui';
import styles from './acceso.module.css';
import { TERMS_ERROR } from './lib';

// Same addresses as the footer of the public shell.
export const TERMS_URL = 'https://onecreator.app/terminos';
export const PRIVACY_URL = 'https://onecreator.app/privacidad';

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
            <a className="lnk" href={TERMS_URL} target="_blank" rel="noreferrer">
              Términos
            </a>{' '}
            y la{' '}
            <a className="lnk" href={PRIVACY_URL} target="_blank" rel="noreferrer">
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
