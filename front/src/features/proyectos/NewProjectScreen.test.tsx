import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { server } from '@/api/mocks/server';
import { USER_IDS } from '@/api/mocks/seed';
import { NewProjectScreen } from './NewProjectScreen';
import { DEFAULT_CONFIG, saveLastConfig } from './new-project-config';
import { renderScreen, startScenario } from './test-utils';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const video = (name: string, type = 'video/mp4') => new File(['x'.repeat(64)], name, { type });
const summary = () => screen.findByRole('complementary', { name: 'Resumen del proyecto' });

async function open(scenario: string) {
  startScenario(scenario);
  const user = userEvent.setup({ applyAccept: false });
  renderScreen(<NewProjectScreen />);
  // The summary appears once the pricing values arrived.
  await screen.findByText('Costo estimado');
  return user;
}

async function pick(user: ReturnType<typeof userEvent.setup>, file: File) {
  await user.upload(screen.getByLabelText('Elegir video'), file);
}

describe('C1 · Nuevo proyecto', () => {
  it('con un archivo muestra el plan, el costo de estimateCost() y el saldo después', async () => {
    const user = await open('estudio-norte');
    expect(screen.getByRole('heading', { level: 1, name: 'Nuevo proyecto' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeDisabled();
    expect(within(await summary()).getByText('Elegí el video para seguir.')).toBeInTheDocument();

    await pick(user, video('podcast-ep42-72min.mp4'));
    expect(await screen.findByText(/Entra en tu plan Pro: hasta/)).toBeInTheDocument();
    const side = await summary();
    // 4.320 s × 0,5 × (1 − 10 %) = 1.944
    await waitFor(() => expect(within(side).getByTestId('cost-total')).toHaveTextContent('1.944'));
    expect(within(side).getByText('1:12:00')).toBeInTheDocument();
    expect(within(side).getByText('Pro · sin marca de agua')).toBeInTheDocument();
    expect(within(side).getByText('2.916')).toBeInTheDocument();
    expect(within(side).getByText('No se renderiza nada hasta que apruebes los clips.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeEnabled();

    await user.click(within(side).getByRole('button', { name: 'Ver detalle' }));
    expect(within(side).getByText('4.320 s × 0,50')).toBeInTheDocument();
    expect(within(side).getByText('Descuento Pro 10 %')).toBeInTheDocument();
    expect(within(side).getByText('−216')).toBeInTheDocument();

    // Resolution and SEO move the estimate.
    await user.click(screen.getByRole('radio', { name: /1080p/ }));
    await waitFor(() => expect(within(side).getByTestId('cost-total')).toHaveTextContent('2.916'));
    await user.click(screen.getByRole('button', { name: /Inteligencia/ }));
    await user.click(screen.getByRole('switch', { name: 'SEO, suma 5 tokens' }));
    await waitFor(() => expect(within(side).getByTestId('cost-total')).toHaveTextContent('2.920,50'));
    expect(within(side).getByText('+5 tokens')).toBeInTheDocument();
  });

  it('«Editar el video completo» se deshabilita con el texto del mapa para un link o un archivo largo', async () => {
    const user = await open('estudio-norte');
    const whole = () => screen.getByRole('radio', { name: /Editar el video completo/ });
    expect(whole()).not.toHaveAttribute('aria-disabled');

    await pick(user, video('corto-8min.mp4'));
    await screen.findByText(/Entra en tu plan Pro/);
    await user.click(whole());
    expect(whole()).toHaveAttribute('aria-checked', 'true');
    // The Clips section belongs to "Sacar clips" only.
    expect(screen.queryByRole('heading', { name: 'Clips' })).not.toBeInTheDocument();
    expect(within(await summary()).getByText('Se renderiza sin pasar por la revisión.')).toBeInTheDocument();

    await user.upload(screen.getByLabelText('Elegir otro video'), video('largo-45min.mp4'));
    expect(await screen.findByText('Para editar el video completo, el video puede durar hasta 10 min')).toBeInTheDocument();
    expect(whole()).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('radio', { name: /Sacar clips/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('heading', { name: 'Clips' })).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /Pegar link/ }));
    expect(screen.getByText('Para editar el video completo, subí el archivo')).toBeInTheDocument();
    expect(whole()).toHaveAttribute('aria-disabled', 'true');
    // Links are processed in 720p.
    expect(screen.getByRole('radio', { name: /1080p/ })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Los proyectos por link se procesan en 720p. Para 1080p o 4K, subí el archivo.')).toBeInTheDocument();
  });

  it('valida el archivo en el navegador antes de subir (sección 4)', async () => {
    const user = await open('estudio-norte');
    await pick(user, new File(['x'], 'guion-ep42.pdf', { type: 'application/pdf' }));
    const row = await screen.findByTestId('file-row');
    expect(within(row).getByRole('alert')).toHaveTextContent('Este archivo no es un video.');
    expect(within(row).getByText('Aceptamos MP4, MOV o AVI.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeDisabled();
    expect(within(await summary()).getByText('Elegí otro archivo para seguir.')).toBeInTheDocument();

    await user.upload(screen.getByLabelText('Elegir otro video'), video('entrevista-200min.mp4'));
    await waitFor(() => expect(within(screen.getByTestId('file-row')).getByRole('alert')).toHaveTextContent('El video dura más de lo que permite tu plan (máx. 3 h).'));
    expect(within(screen.getByTestId('file-row')).getByText(/El plan Estudio acepta videos de hasta/)).toBeInTheDocument();
    const side = await summary();
    expect(within(side).getByLabelText('Sin estimación')).toBeInTheDocument();
    expect(within(side).getByText('Lo calculamos cuando elijas un video que entre en tu plan.')).toBeInTheDocument();
    expect(within(side).getByText(/El video supera el máximo de tu plan/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeDisabled();
  });

  it('pegar link: reconoce el video y muestra los links que no sirven junto al campo', async () => {
    const user = await open('estudio-norte');
    await user.click(screen.getByRole('tab', { name: /Pegar link/ }));
    const input = screen.getByRole('textbox', { name: 'Link del video' });
    expect(screen.getByText('YouTube, Vimeo y otros sitios públicos.')).toBeInTheDocument();

    await user.type(input, 'https://www.youtube.com/watch?v=k3Vn8sQx2Lw');
    const row = await screen.findByTestId('link-row', {}, { timeout: 3000 });
    expect(within(row).getByText('YouTube · 45:00')).toBeInTheDocument();
    const side = await summary();
    expect(within(side).getByText('Link de YouTube')).toBeInTheDocument();
    // Flat price of a link project.
    await waitFor(() => expect(within(side).getByTestId('cost-total')).toHaveTextContent('10'));
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeEnabled();

    await user.click(within(row).getByRole('button', { name: 'Quitar' }));
    expect(input).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeDisabled();
  }, 20_000);

  it('pegar link: los links que no sirven se explican junto al campo', async () => {
    const user = await open('estudio-norte');
    await user.click(screen.getByRole('tab', { name: /Pegar link/ }));
    const input = screen.getByRole('textbox', { name: 'Link del video' });
    await user.type(input, 'https://www.youtube.com/watch?v=privado');
    expect(await screen.findByText('No pudimos acceder a ese video: es privado, no existe o pide iniciar sesión.', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(input).toHaveAttribute('aria-invalid', 'true');

    await user.clear(input);
    await user.type(input, 'https://intranet.estudionorte.com/videos/reunion');
    expect(await screen.findByText(/Todavía no podemos traer videos de ese sitio/, {}, { timeout: 3000 })).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, 'https://www.youtube.com/watch?v=largo');
    expect(await screen.findByText('El video dura más de lo que permite tu plan (máx. 3 h).', {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toBeDisabled();

    await user.clear(input);
    await user.type(input, 'youtube');
    expect(await screen.findByText('Pegá el link completo, empezando por https://', {}, { timeout: 3000 })).toBeInTheDocument();
  }, 30_000);

  it('describir con IA: pide el texto y estima un rango según la resolución', async () => {
    const user = await open('estudio-norte');
    await user.click(screen.getByRole('tab', { name: /Describir con IA/ }));
    expect(screen.queryByRole('heading', { name: 'Qué querés hacer' })).not.toBeInTheDocument();
    expect(screen.getByText('La arma la IA a partir de tu texto.')).toBeInTheDocument();

    await pick(user, video('podcast-ep42-72min.mp4'));
    const side = await summary();
    expect(await within(side).findByText('Entre 1.944 y 5.832')).toBeInTheDocument();
    expect(within(side).getByText('La define tu texto')).toBeInTheDocument();
    expect(within(side).getByText('Ninguno: SEO apagado')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Crear proyecto' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Contanos qué querés');
    const prompt = screen.getByRole('textbox', { name: 'Contanos qué querés' });
    expect(prompt).toHaveFocus();
    await user.type(prompt, 'Sacá 5 clips graciosos');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText('22 / 1000')).toBeInTheDocument();
  });

  it('layout: el bloque Avanzado aparece solo con Dividido o Versus', async () => {
    const user = await open('estudio-norte');
    await user.click(screen.getByRole('button', { name: /^Estilo/ }));
    expect(screen.queryByRole('button', { name: /Avanzado/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'Dividido' }));
    const advanced = screen.getByRole('button', { name: /Avanzado/ });
    expect(advanced).toHaveAttribute('aria-expanded', 'false');
    await user.click(advanced);
    expect(screen.getByRole('group', { name: 'Posición del gameplay' })).toBeInTheDocument();
    const side = screen.getByRole('group', { name: 'Lado de la cámara' });
    expect(within(side).getAllByRole('button').map((item) => item.textContent)).toEqual(['Izquierda', 'Centro', 'Derecha']);
    expect(screen.queryByRole('slider', { name: 'Zoom' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('switch', { name: 'Cámara manual' }));
    expect(screen.getByRole('slider', { name: 'Zoom' })).toHaveAttribute('max', '4');
    expect(screen.getByRole('slider', { name: 'Centro horizontal' })).toHaveValue('0.5');
    expect(screen.getByRole('slider', { name: 'Centro vertical' })).toHaveValue('0.2');
    expect(screen.getByText(/la cámara manual sigue apagada/)).toBeInTheDocument();
    // The manual camera replaces the chosen side.
    expect(within(side).getByRole('button', { name: 'Izquierda' })).toHaveAttribute('aria-disabled', 'true');

    await user.click(screen.getByRole('radio', { name: 'Versus' }));
    await user.click(screen.getByRole('button', { name: /Avanzado/ }));
    expect(screen.queryByRole('group', { name: 'Posición del gameplay' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Lado de la cámara' })).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Cámara manual' })).toBeInTheDocument();

    // Versus outside 9:16 leaves black at the sides, and the drawing shows it.
    await user.click(screen.getByRole('radio', { name: /16:9/ }));
    expect(screen.getByText(/cada mitad conserva al menos el/)).toBeInTheDocument();

    await user.click(screen.getByRole('radio', { name: 'Quien habla' }));
    expect(screen.queryByRole('button', { name: /Avanzado/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Inteligencia/ }));
    const faces = screen.getByRole('switch', { name: 'Seguir caras' });
    expect(faces).toBeChecked();
    expect(faces).toBeDisabled();
  });

  it('subtítulos: la muestra cambia en vivo y «Renderizar sin revisar» explica el cobro', async () => {
    const user = await open('estudio-norte');
    await pick(user, video('corto-8min.mp4'));
    await screen.findByText(/Entra en tu plan Pro/);
    await user.click(screen.getByRole('button', { name: /Subtítulos/ }));
    const sample = screen.getByTestId('caption-sample');
    expect(sample).toHaveTextContent('la regla delos tres sí');
    await user.click(screen.getByRole('button', { name: 'Una palabra menos' }));
    expect(sample).toHaveTextContent('la reglade los');
    await user.click(screen.getByRole('button', { name: 'Blanco' }));
    expect(screen.getByRole('button', { name: 'Blanco' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /Subtítulos/ })).toHaveTextContent('Activados · grandes · abajo · blanco · 2 palabras por línea');
    await user.click(screen.getByRole('switch', { name: 'Mostrar subtítulos' }));
    expect(screen.queryByTestId('caption-sample')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Inteligencia/ }));
    const bypass = screen.getByRole('switch', { name: 'Renderizar sin revisar' });
    expect(bypass).not.toBeChecked();
    expect(screen.getByText(/Apagado: el proyecto se detiene en la revisión/)).toBeInTheDocument();
    await user.click(bypass);
    expect(
      screen.getByText('El render de los clips se reserva apenas estén listos, al mismo precio que aprobarlos, y se cobra solo lo que se entrega. Si no alcanza el saldo, el proyecto espera tu revisión.'),
    ).toBeInTheDocument();
    expect(within(await summary()).getByText('Se renderiza sin pasar por la revisión.')).toBeInTheDocument();
  });

  it('plan Gratis: candados de resolución y SEO, marca de agua y límite de 10 min', async () => {
    const user = await open('plan-gratis');
    expect(await screen.findByText('Tu plan Gratis incluye 720p.')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /1080p, disponible en el plan Creador/ })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('radio', { name: /4K, disponible en el plan Pro/ })).toHaveAttribute('aria-disabled', 'true');
    const side = await summary();
    expect(within(side).getByText('Tu plan agrega marca de agua')).toBeInTheDocument();
    expect(within(side).getByText('Gratis · con marca de agua')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Inteligencia/ }));
    expect(screen.getByRole('switch', { name: 'SEO, disponible en el plan Creador' })).toBeDisabled();

    await pick(user, video('primer-video-8min.mp4'));
    expect(await screen.findByText(/Entra en tu plan Gratis: hasta/)).toBeInTheDocument();
    await waitFor(() => expect(within(side).getByTestId('cost-total')).toHaveTextContent('240'));
    await user.click(within(side).getByRole('button', { name: 'Ver detalle' }));
    expect(within(side).getByText('No incluye')).toBeInTheDocument();
    expect(within(side).getByText('60')).toBeInTheDocument();

    await user.upload(screen.getByLabelText('Elegir otro video'), video('clase-30min.mp4'));
    await waitFor(() => expect(within(screen.getByTestId('file-row')).getByRole('alert')).toHaveTextContent('El video dura más de lo que permite tu plan (máx. 10 min).'));
    expect(within(screen.getByTestId('file-row')).getByText(/El plan Creador acepta videos de hasta/)).toBeInTheDocument();
  });

  it('saldo bajo: el botón pasa a «Comprar tokens»', async () => {
    const user = await open('saldo-bajo');
    await pick(user, video('podcast-ep42-72min.mp4'));
    const side = await summary();
    const buy = await within(side).findByRole('link', { name: 'Comprar tokens' });
    expect(buy).toHaveAttribute('href', '/uso/comprar');
    expect(within(side).getByText('Te faltan')).toBeInTheDocument();
    expect(within(side).getByText('1.824')).toBeInTheDocument();
    expect(within(side).getByText(/Necesitás/)).toHaveTextContent('Necesitás 1.944 tokens y tenés 120.');
    expect(screen.queryByRole('button', { name: 'Crear proyecto' })).not.toBeInTheDocument();
  });

  it('rol Lector: ve el formulario pero no puede crear', async () => {
    const user = await open('rol-lector');
    expect(screen.getByText('Tu rol no puede crear proyectos')).toBeInTheDocument();
    await pick(user, video('corto-8min.mp4'));
    await screen.findByText(/Entra en tu plan Pro/);
    expect(screen.getByRole('button', { name: 'Crear proyecto' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('propone la última configuración usada en este navegador', async () => {
    saveLastConfig(USER_IDS.lucia, { ...DEFAULT_CONFIG, editing_style: 'hormozi', render_layout: 'versus', aspect_ratio: '1:1', max_clips: 12, subtitle_size: 'small' });
    await open('estudio-norte');
    const last = await screen.findByRole('radio', { name: /Mi última configuración/ });
    expect(last).toHaveAttribute('aria-checked', 'true');
    expect(last).toHaveTextContent('Hormozi · Versus');
    expect(last).toHaveTextContent('Subtítulos chicos');
    expect(screen.getByRole('radio', { name: /1:1/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('textbox', { name: 'Cantidad máxima' })).toHaveValue('12');
    expect(screen.getByText('Valores de «Mi última configuración». Abrí lo que quieras cambiar.')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole('radio', { name: /^Clase/ }));
    expect(screen.getByRole('button', { name: /^Estilo/ })).toHaveTextContent('Minimalista · Ajustar');
    expect(screen.getByText('Valores de «Clase». Abrí lo que quieras cambiar.')).toBeInTheDocument();
  });
});
