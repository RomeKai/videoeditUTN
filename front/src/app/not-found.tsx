import type { Metadata } from 'next';
import { Button } from '@/ui/Button';
import { SystemScreen } from '@/shell/SystemPage';

export const metadata: Metadata = { title: 'Esta página no existe' };

/** K1 · No encontrado. */
export default function NotFound() {
  return (
    <SystemScreen
      title="Esta página no existe"
      code="404"
      description="Puede que el link esté mal escrito o que el proyecto se haya borrado."
      actions={
        <Button variant="primary" href="/proyectos">
          Ir a Proyectos
        </Button>
      }
    />
  );
}
