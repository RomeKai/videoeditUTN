# Pantallas por dominio

Cada dominio de [`ROUTES.md`](../../ROUTES.md) pone acá su código: `src/features/<dominio>/`. Los `page.tsx` de `src/app/` quedan finos: importan la pantalla desde su carpeta de dominio.

Lo compartido (componentes, shell, diálogos, API, utilidades) vive en `src/ui`, `src/shell`, `src/dialogs`, `src/api` y `src/lib`, y no se toca desde un dominio. Si a una pantalla le falta algo compartido, se arma dentro de su carpeta y se anota como candidato a promover.
