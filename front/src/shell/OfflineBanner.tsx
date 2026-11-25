'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { FixedBanner } from '@/ui/Banner';
import styles from './System.module.css';

/** K4: fixed banner while the browser is offline. It goes away by itself and refreshes what is on screen. */
export function OfflineBanner() {
  const [offline, setOffline] = useState(false);
  const queryClient = useQueryClient();
  useEffect(() => {
    const update = () => setOffline(!window.navigator.onLine);
    const onOnline = () => {
      setOffline(false);
      void queryClient.invalidateQueries();
    };
    update();
    window.addEventListener('offline', update);
    window.addEventListener('online', onOnline);
    return () => {
      window.removeEventListener('offline', update);
      window.removeEventListener('online', onOnline);
    };
  }, [queryClient]);
  if (!offline) return null;
  return (
    <div className={styles.offline}>
      <FixedBanner icon="offline" pulsing>
        Sin conexión. Reintentando
      </FixedBanner>
    </div>
  );
}
