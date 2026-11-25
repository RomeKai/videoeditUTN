'use client';

import { useTheme, type ThemePreference } from '@/lib/theme';
import { IconButton } from './Button';
import { SegmentedControl } from './SegmentedControl';

/** Icon button that flips between dark and light and remembers the choice. */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme();
  return (
    <IconButton
      className={className}
      icon={theme === 'dark' ? 'sun' : 'moon'}
      label={theme === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro'}
      onClick={toggle}
    />
  );
}

/** Apariencia in Perfil: Sistema, Oscuro, Claro. */
export function ThemePreferenceControl() {
  const { preference, setPreference } = useTheme();
  return (
    <SegmentedControl<ThemePreference>
      ariaLabel="Apariencia"
      value={preference}
      onChange={setPreference}
      options={[
        { value: 'system', label: 'Sistema', icon: 'monitor' },
        { value: 'dark', label: 'Oscuro', icon: 'moon' },
        { value: 'light', label: 'Claro', icon: 'sun' },
      ]}
    />
  );
}
