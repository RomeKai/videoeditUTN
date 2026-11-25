import type { ReactNode } from 'react';

/**
 * Layout of the project's tabs (Resumen, Clips, Transcripción, Actividad and
 * Resultado). Dominio dueño: `proyecto`. Acá va la cabecera única del proyecto
 * (`<ProjectHeader>` de `@/ui`) y las sub-pestañas (`projectTabs()` de
 * `@/shell/nav` con `<NavTabs>`), para que solo cambie el contenido de la pestaña.
 * Revisión (D2) y Editor (D4) quedan fuera de este grupo: usan la cabecera compacta.
 */
export default function ProjectTabsLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
