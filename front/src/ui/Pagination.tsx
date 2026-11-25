'use client';

import { cx } from '@/lib/cx';
import { formatNumber } from '@/lib/format';
import { Button } from './Button';
import styles from './Patterns.module.css';

export interface PaginationProps {
  /** 1-based. */
  page: number;
  pageSize: number;
  /** Total items. */
  count: number;
  onPageChange: (page: number) => void;
  /** Plural noun for the summary: "proyectos". */
  noun?: string;
  className?: string;
}

/** "1–25 de 132 proyectos" with previous/next. Lists use 25 per page (map B2). */
export function Pagination({ page, pageSize, count, onPageChange, noun = 'resultados', className }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  const from = count === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(count, page * pageSize);
  return (
    <nav className={cx(styles.pagination, className)} aria-label="Paginación">
      <span className="num">
        {formatNumber(from, 0)}–{formatNumber(to, 0)} de {formatNumber(count, 0)} {noun}
      </span>
      <span className={styles.paginationActions}>
        <Button size="sm" variant="secondary" iconLeft="chevron-left" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          Anterior
        </Button>
        <span className="num" aria-current="page">
          {page} / {pages}
        </span>
        <Button size="sm" variant="secondary" iconRight="chevron-right" disabled={page >= pages} onClick={() => onPageChange(page + 1)}>
          Siguiente
        </Button>
      </span>
    </nav>
  );
}
