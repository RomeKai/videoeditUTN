import Link from 'next/link';
import { cx } from '@/lib/cx';
import styles from './System.module.css';

/** OneCreator wordmark. As a link it goes to Proyectos (or wherever `href` says). */
export function Brand({ href, label, className }: { href?: string; label?: string; className?: string }) {
  const content = (
    <>
      <span className={styles.brandMark} aria-hidden="true" />
      <span>OneCreator</span>
    </>
  );
  if (!href) return <span className={cx(styles.brand, className)}>{content}</span>;
  return (
    <Link href={href} className={cx(styles.brand, className)} aria-label={label ?? 'OneCreator, ir a Proyectos'}>
      {content}
    </Link>
  );
}
