'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import type { NotificationPreferences } from '@/api/contracts/users';
import { useNotificationPreferences, useUpdateNotificationPreferences } from '@/api/hooks';
import { keys } from '@/api/query';
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
  {
    key: 'post_failed',
    label: 'Una publicación falló',
    description: 'Un clip no salió en una de tus redes, o no pudimos confirmar si salió. Te contamos qué pasó para que la reintentes.',
    when: 'una publicación falla',
  },
  { key: 'low_balance', label: 'Te queda poco saldo', description: 'El saldo está por debajo de lo que cuesta un video promedio.', when: 'te queda poco saldo' },
];

/** I2 · Perfil › Notificaciones: one email switch per type; each change saves itself. */
export function NotificationsScreen() {
  const toast = useToast();
  const titleId = useId();
  const { me } = useSession();
  const preferences = useNotificationPreferences();
  const update = useUpdateNotificationPreferences();
  const queryClient = useQueryClient();
  // What the user just flipped, shown right away while the request is in flight.
  const [optimistic, setOptimistic] = useState<Partial<NotificationPreferences>>({});

  // One counter per switch: only the answer to the last click of a switch may touch it, whatever
  // order the answers arrive in.
  const sequence = useRef<Partial<Record<PreferenceKey, number>>>({});
  const inFlight = useRef(0);

  const toggle = (key: PreferenceKey, when: string, checked: boolean) => {
    const turn = (sequence.current[key] ?? 0) + 1;
    sequence.current[key] = turn;
    const latest = () => sequence.current[key] === turn;
    setOptimistic((current) => ({ ...current, [key]: checked }));
    inFlight.current += 1;
    // mutateAsync, not mutate's callbacks: those only run for the last call, and two switches can be in flight.
    update
      .mutateAsync({ [key]: checked })
      .then(
        () => {
          if (latest()) toast.success(checked ? `Guardado. Te avisamos por email cuando ${when}.` : `Guardado. Ya no te avisamos por email cuando ${when}.`);
        },
        (error: unknown) => {
          if (latest()) toast.error(errorMessage(error, 'No pudimos guardar el cambio'));
        },
      )
      .then(() => {
        inFlight.current -= 1;
        // The hook writes each answer to the cache as it arrives, so a slow, older answer can
        // overwrite a newer one. Once nothing is in flight, ask for the truth and only then stop
        // showing what the user chose.
        if (inFlight.current > 0) return undefined;
        return queryClient.invalidateQueries({ queryKey: keys.notificationPreferences }).finally(() => {
          if (inFlight.current === 0) setOptimistic({});
        });
      })
      .catch(() => undefined);
  };

  return (
    <section className={styles.group} aria-labelledby={titleId}>
      <div className={styles.head}>
        <div className={styles.headText}>
          <h2 className="t-h3" id={titleId}>
            Emails que querés recibir
          </h2>
          <p className="t-small muted">
            {me
              ? me.is_email_verified
                ? `Llegan a ${me.email}. `
                : `Todavía no verificaste ${me.email}: hasta que lo hagas no te mandamos estos emails. `
              : ''}
            Vienen todos activados y cada cambio se guarda solo.
          </p>
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
      <p className={styles.note}>En la app, estos avisos aparecen siempre en la campana, aunque apagues el email.</p>
    </section>
  );
}
