import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { EdgeInput } from './EdgeInput';

function Harness({ onCommit }: { onCommit?: (seconds: number) => void }) {
  const [value, setValue] = useState(81.7);
  return (
    <>
      <button type="button" onClick={() => setValue((current) => current - 0.5)}>
        antes
      </button>
      <label>
        Inicio
        <EdgeInput
          value={value}
          onCommit={(seconds) => {
            onCommit?.(seconds);
            setValue(seconds);
          }}
        />
      </label>
    </>
  );
}

describe('EdgeInput', () => {
  it('muestra el valor nuevo en el mismo instante en que cambia, sin un render de atraso', async () => {
    render(<Harness />);
    const input = screen.getByRole('textbox', { name: 'Inicio' }) as HTMLInputElement;
    expect(input.value).toBe('01:21,7');
    // A real click, outside React's test batching: whoever reads the field right after it (a person's
    // next keystroke, an automated check) must already see the new time.
    screen.getByRole('button', { name: 'antes' }).dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    // React commits a click's update before the browser runs its next task. Only microtasks are
    // flushed here: a field that needed one more scheduled render would still show the old time.
    for (let tick = 0; tick < 5; tick += 1) await Promise.resolve();
    expect(input.value).toBe('01:21,2');
  });

  it('lo que se escribe se confirma con Enter y lo que no es un tiempo vuelve al valor', async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    const input = screen.getByRole('textbox', { name: 'Inicio' }) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, 'no es un tiempo');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    await user.keyboard('{Enter}');
    expect(input.value).toBe('01:21,7');
    expect(onCommit).not.toHaveBeenCalled();
    await user.clear(input);
    await user.type(input, '1:30{Enter}');
    expect(onCommit).toHaveBeenCalledWith(90);
    expect(input.value).toBe('01:30,0');
    // Escape drops what is being typed.
    await user.type(input, '99');
    await user.keyboard('{Escape}');
    expect(input.value).toBe('01:30,0');
  });
});
