import type { Metadata } from 'next';
import { NotificationsScreen } from '@/features/ajustes/NotificationsScreen';

export const metadata: Metadata = { title: 'Notificaciones' };

/** I2 · Perfil › Notificaciones. */
export default function Page() {
  return <NotificationsScreen />;
}
