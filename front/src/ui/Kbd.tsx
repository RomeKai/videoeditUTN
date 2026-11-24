import type { ReactNode } from 'react';
import styles from './Kbd.module.css';

/** Keyboard shortcut cap (Geist Mono). */
export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className={styles.kbd}>{children}</kbd>;
}
