import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Field, Input, PasswordInput, Select, Textarea } from './Field';
import { FileDropZone } from './FileDropZone';
import { Stepper } from './Stepper';

describe('Field', () => {
  it('une la etiqueta y la ayuda con el control', () => {
    render(
      <Field label="Link del video" hint="YouTube, Vimeo y otros sitios públicos.">
        <Input />
      </Field>,
    );
    const input = screen.getByLabelText('Link del video');
    expect(input).toHaveAccessibleDescription('YouTube, Vimeo y otros sitios públicos.');
    expect(input).not.toHaveAttribute('aria-invalid');
  });

  it('con error marca el control como inválido, anuncia el mensaje y oculta la ayuda', () => {
    render(
      <Field label="Email" hint="El de tu cuenta." error="Revisá el email: falta el dominio.">
        <Input defaultValue="lucia@estudionorte" />
      </Field>,
    );
    const input = screen.getByLabelText('Email');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAccessibleDescription('Revisá el email: falta el dominio.');
    expect(screen.getByRole('alert')).toHaveTextContent('Revisá el email: falta el dominio.');
    expect(screen.queryByText('El de tu cuenta.')).not.toBeInTheDocument();
  });

  it('el error aparece y desaparece al corregir', async () => {
    const user = userEvent.setup();
    function Form() {
      const [value, setValue] = useState('');
      return (
        <Field label="Qué querés" error={value.trim() ? undefined : 'Contanos qué querés'}>
          <Textarea value={value} onChange={(event) => setValue(event.target.value)} />
        </Field>
      );
    }
    render(<Form />);
    const textarea = screen.getByLabelText('Qué querés');
    expect(textarea).toHaveAttribute('aria-invalid', 'true');
    await user.type(textarea, 'Clips graciosos');
    expect(textarea).not.toHaveAttribute('aria-invalid');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('sirve igual para select, con un primer valor vacío sin elegir', () => {
    render(
      <Field label="Quién puede ver el video" error="Elegí quién puede ver el video.">
        <Select defaultValue="" placeholder="Elegí una opción" options={[{ value: 'SELF_ONLY', label: 'Solo yo' }]} />
      </Field>,
    );
    const select = screen.getByLabelText('Quién puede ver el video');
    expect(select).toHaveValue('');
    expect(select).toHaveAttribute('aria-invalid', 'true');
  });

  it('la contraseña se puede mostrar y ocultar', async () => {
    const user = userEvent.setup();
    render(
      <Field label="Contraseña">
        <PasswordInput defaultValue="secreta" />
      </Field>,
    );
    const input = screen.getByLabelText('Contraseña');
    expect(input).toHaveAttribute('type', 'password');
    await user.click(screen.getByRole('button', { name: 'Mostrar la contraseña' }));
    expect(input).toHaveAttribute('type', 'text');
    expect(screen.getByRole('button', { name: 'Ocultar la contraseña' })).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('Stepper', () => {
  it('respeta el mínimo y el máximo', async () => {
    const user = userEvent.setup();
    function Counter() {
      const [value, setValue] = useState(19);
      return <Stepper value={value} onChange={setValue} min={1} max={20} decrementLabel="Un clip menos" incrementLabel="Un clip más" />;
    }
    render(<Counter />);
    await user.click(screen.getByRole('button', { name: 'Un clip más' }));
    expect(screen.getByRole('textbox')).toHaveValue('20');
    expect(screen.getByRole('button', { name: 'Un clip más' })).toBeDisabled();
    await user.clear(screen.getByRole('textbox'));
    await user.type(screen.getByRole('textbox'), '99');
    expect(screen.getByRole('textbox')).toHaveValue('20');
  });
});

describe('FileDropZone', () => {
  it('entrega el archivo elegido y el soltado', async () => {
    const user = userEvent.setup();
    const onFiles = vi.fn();
    render(<FileDropZone onFiles={onFiles} accept=".mp4" inputLabel="Elegir video" />);
    const file = new File(['x'], 'clase.mp4', { type: 'video/mp4' });
    await user.upload(screen.getByLabelText('Elegir video'), file);
    expect(onFiles).toHaveBeenCalledWith([file]);
  });

  it('deshabilitada no acepta archivos', () => {
    render(<FileDropZone onFiles={vi.fn()} disabled inputLabel="Elegir video" />);
    expect(screen.getByLabelText('Elegir video')).toBeDisabled();
  });
});
