import { db } from '@/testing';

export { renderScreen, signIn } from '@/testing';

/** Id of a seeded project, by the start of its title. */
export function projectId(title: string): string {
  const project = db().projects.find((item) => item.title.startsWith(title));
  if (!project) throw new Error(`No hay proyecto «${title}» en el escenario`);
  return project.id;
}
