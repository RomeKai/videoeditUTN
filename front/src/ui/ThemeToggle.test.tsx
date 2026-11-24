import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { THEME_STORAGE_KEY, ThemeProvider } from '@/lib/theme';
import { ThemePreferenceControl, ThemeToggle } from './ThemeToggle';

function mockSystem(light: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: light && query.includes('light'),
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

describe('tema', () => {
  it('arranca en oscuro, cambia a claro y recuerda la elección', async () => {
    const user = userEvent.setup();
    mockSystem(false);
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    await user.click(screen.getByRole('button', { name: 'Cambiar a tema claro' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
    await user.click(screen.getByRole('button', { name: 'Cambiar a tema oscuro' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('aplica el tema guardado al volver', () => {
    mockSystem(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light');
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    );
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    expect(screen.getByRole('button', { name: 'Cambiar a tema oscuro' })).toBeInTheDocument();
  });

  it('«Sistema» sigue al dispositivo', async () => {
    const user = userEvent.setup();
    mockSystem(true);
    render(
      <ThemeProvider>
        <ThemePreferenceControl />
      </ThemeProvider>,
    );
    expect(screen.getByRole('button', { name: 'Oscuro' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: 'Sistema' }));
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('system');
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
  });
});
