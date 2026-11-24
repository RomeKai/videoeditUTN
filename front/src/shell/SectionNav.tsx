'use client';

import { SideNav } from '@/ui/Settings';
import { PROFILE_NAV, SETTINGS_NAV } from './nav';
import { usePermissions } from './session';

/** Side navigation of Ajustes. The admin sections are not shown to Editor and Lector (map 1.7). */
export function SettingsNav() {
  const { can } = usePermissions();
  const admin = can('members.manage');
  return <SideNav ariaLabel="Secciones de ajustes" items={SETTINGS_NAV.filter((item) => admin || !item.adminOnly)} />;
}

export function ProfileNav() {
  return <SideNav ariaLabel="Secciones de la cuenta" items={PROFILE_NAV} />;
}
