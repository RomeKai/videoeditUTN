import { Skeleton, SkeletonRows } from '@/ui/Skeleton';
import { Brand } from './Brand';
import styles from './System.module.css';

/** Global loading: the outline of the app, never a full-screen spinner (map 1.8). */
export function GlobalLoading() {
  return (
    <div className={styles.loading} role="status" aria-label="Cargando OneCreator" aria-busy="true">
      <div className={styles.loadingTop}>
        <Brand />
      </div>
      <div className={styles.loadingBody}>
        <Skeleton width={180} height={28} />
        <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)' }}>
          <SkeletonRows rows={4} label="Cargando" />
        </div>
      </div>
    </div>
  );
}

/** Loading of a page inside the app shell (used by route `loading.tsx` files). */
export function PageLoading({ rows = 4 }: { rows?: number }) {
  return (
    <div className={styles.loadingBody} role="status" aria-label="Cargando" aria-busy="true">
      <Skeleton width={220} height={28} />
      <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--r-md)' }}>
        <SkeletonRows rows={rows} label="Cargando" />
      </div>
    </div>
  );
}
