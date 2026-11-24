'use client';

import { useId, useState } from 'react';
import type { NotificationPreferences } from '@/api/contracts/users';
import { useNotificationPreferences, useUpdateNotificationPreferences } from '@/api/hooks';
import { cx } from '@/lib/cx';
import { errorMessage } from '@/lib/error-messages';
import { LoadError } from '@/shell/LoadError';
import { useSession } from '@/shell/session';
import { Skeleton, Switch, useToast } from '@/ui';
import styles from './ajustes.module.css';

type PreferenceKey = keyof NotificationPreferences;

/** One switch per case of map 1.11. `when` completes "te avisamos por email cuando …". */
export const NOTIFICATION_TYPES: Array<{ key: PreferenceKey; label: string; description: string; when: string }> = [
  {
    key: 'project_ready',
    label: 'El proyecto está listo para revisar',
    description: 'La IA terminó de buscar los mejores momentos y los clips te esperan.',
    when: 'un proyecto está listo para revisar',
  },
  { key: 'render_finished', label: 'El render terminó', description: 'Los clips que aprobaste ya se pueden descargar o publicar.', when: 'termina un render' },
  { key: 'project_failed', label: 'El proyecto falló', description: 'Te contamos qué pasó y cuántos tokens te devolvimos.', when: 'un proyecto falla' },
  { key: 'post_failed', label: 'Una publicación falló', description: 'Un clip no se pudo publicar en una de tus redes.', when: 'una publicación falla' },
  { key: 'low_balance', label: 'Te queda poco saldo', description: 'El saldo está por debajo de lo que cuesta un video promedio.', when: 'te queda poco saldo' },
];

/** I2 · Perfil › Notificaciones: one email switch per type; each change saves itself. */
export function NotificationsScreen() {
  const toast = useToast();
  const titleId = useId();
  const { me } = useSession();
  const preferences = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  // What the user just flipped, shown right away while the request is in flight.
  const [optimistic, setOptimistic] = useState<Partial<NotificationPreferences>>({});

  const toggle = (key: PreferenceKey, when: string, checked: boolean) => {
    setOptimistic((current) => ({ ...current, [key]: checked }));
    update.mutate(
      { [key]: checked },
      {
        onSuccess: () => toast.success(checked ? `Guardado. Te avisamos por email cuando ${when}.` : `Guardado. Ya no te avisamos por email cuando ${when}.`),
        onError: (error) => toast.error(errorMessage(error, 'No pudimos guardar el cambio')),
        onSettled: () =>
          setOptimistic((current) => {
            const { [key]: _done, ...rest } = current;
            void _done;
            return rest;
          }),
      },
    );
  };

  return (
    <section className={styles.group} aria-labelledby={titleId}>
      <div className={styles.head}>
        <div className={styles.headText}>
          <h2 className="t-h3" id={titleId}>
            Emails que querés recibir
          </h2>
          <p className="t-small muted">{me ? `Llegan a ${me.email}. ` : ''}Vienen todos activados y cada cambio se guarda solo.</p>
        </div>
      </div>
      {preferences.isError ? <LoadError error={preferences.error} onRetry={() => void preferences.refetch()} className={styles.mem} /> : null}
      {preferences.data ? (
        NOTIFICATION_TYPES.map((type) => (
          <div key={type.key} className={cx(styles.row, styles.rowTight)}>
            <div className={styles.label}>
              <span>{type.label}</span>
              <span className={styles.desc}>{type.description}</span>
            </div>
            <span className={styles.swHit}>
              <Switch
                checked={optimistic[type.key] ?? preferences.data[type.key]}
                onChange={(checked) => toggle(type.key, type.when, checked)}
                ariaLabel={`Email: ${type.label}`}
              />
            </span>
          </div>
        ))
      ) : preferences.isError ? null : (
        <div aria-busy="true">
          <span className="sr-only">Cargando tus preferencias</span>
          {NOTIFICATION_TYPES.map((type) => (
            <div key={type.key} className={cx(styles.row, styles.rowTight)}>
              <div className={styles.label}>
                <Skeleton width={220} />
                <Skeleton width={300} height={12} />
              </div>
              <Skeleton width={36} height={20} pill />
            </div>
          ))}
        </div>
      )}
      <p className={styles.note}>En la app estos avisos aparecen siempre en la campana, aunque apagues el email.</p>
    </section>
  );
}
