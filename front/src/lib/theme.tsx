'use client';

import { THEME_INIT_SCRIPT, THEME_STORAGE_KEY } from './theme-script';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type ThemePreference = 'system' | 'dark' | 'light';
export type ResolvedTheme = 'dark' | 'light';

export { THEME_INIT_SCRIPT, THEME_STORAGE_KEY };

function readPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'light' || stored === 'system' || stored === 'dark' ? stored : 'dark';
  } catch {
    return 'dark';
  }
}

function systemTheme(): ResolvedTheme {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === 'system' ? systemTheme() : preference;
}

interface ThemeContextValue {
  /** What the user chose in Perfil › Apariencia. */
  preference: ThemePreference;
  /** What is applied right now. */
  theme: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  /** Switches between dark and light (command menu "Cambiar tema", topbar toggle). */
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>('dark');
  const [theme, setTheme] = useState<ResolvedTheme>('dark');
  // False until the stored choice was read: before that the inline script's data-theme must not be overwritten.
  const [ready, setReady] = useState(false);

  // The stored choice is only readable in the browser.
  useEffect(() => {
    const stored = readPreference();
    setPreferenceState(stored);
    setTheme(resolveTheme(stored));
    setReady(true);
  }, []);

  useEffect(() => {
    if (ready && document.documentElement.getAttribute('data-theme') !== theme) document.documentElement.setAttribute('data-theme', theme);
  }, [ready, theme]);

  // "Sistema" follows the OS while the app is open.
  useEffect(() => {
    if (preference !== 'system' || !window.matchMedia) return;
    const media = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => setTheme(media.matches ? 'light' : 'dark');
    media.addEventListener?.('change', onChange);
    return () => media.removeEventListener?.('change', onChange);
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    setTheme(resolveTheme(next));
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Private mode: the choice lasts for this page only.
    }
  }, []);

  const toggle = useCallback(() => setPreference(theme === 'dark' ? 'light' : 'dark'), [setPreference, theme]);

  const value = useMemo(() => ({ preference, theme, setPreference, toggle }), [preference, theme, setPreference, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error('useTheme necesita <ThemeProvider> (ya está en el layout raíz).');
  return value;
}
