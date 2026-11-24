# Sistema visual de OneCreator

Esta carpeta guarda el sistema visual con el que están dibujadas las 42 pantallas del [canvas de diseño](https://claude.ai/artifact/Qj7y6R7mVSAtHPpNRkWiaC). El canvas tiene una pantalla, "01 · Sistema visual", que muestra cada componente en tema oscuro y claro. Este README resume lo que el frontend necesita para portarlo.

- **Qué es cada pantalla y qué decide:** [`../mapa-de-pantallas.md`](../mapa-de-pantallas.md).
- **El CSS del kit (v2.3):** [`onecreator-kit.css`](onecreator-kit.css). Es la referencia de valores; no hace falta usarlo tal cual. Todo está bajo la clase raíz `.oc`, y el tema claro se activa con `data-theme="light"`.

## Tokens

| Grupo | Tokens | Uso |
|---|---|---|
| Superficies | `--bg`, `--bg-subtle`, `--surface`, `--surface-2`, `--surface-3` | Fondo de página, bandas, tarjetas y controles |
| Bordes | `--border`, `--border-strong` | Bordes de 1 px; no se usan sombras salvo en lo que flota (menús, diálogos) |
| Texto | `--fg`, `--fg-muted`, `--fg-subtle` | Principal, secundario, ayudas. Los tres cumplen AA en ambos temas |
| Acción | `--primary`, `--primary-fg` | El botón principal es monocromo: claro sobre oscuro y oscuro sobre claro |
| Acento | `--accent`, `--accent-strong`, `--accent-soft`, `--accent-fill` | Violeta. Solo para "Revisar clips", "Renderizar", el plan recomendado, la selección, los puntajes y el foco |
| Estados | `--ok`, `--warn`, `--err`, `--info` y sus `-soft`; `--err-fill` | Solo para estados. Lo que está en curso va en azul (`--info`) |
| Avatares | `--av-lf`, `--av-ms`, `--av-cr`, `--av-dp`, `--av-ws` | Colores fijos de los datos de ejemplo |
| Radios | `--r-xs` 4, `--r-sm` 6, `--r-md` 8, `--r-lg` 12, `--r-xl` 16 | Controles 6, tarjetas 8, menús y diálogos 12 |
| Tipografía | `--font` (Geist), `--mono` (Geist Mono) | Ver "Números" |

## Reglas

- **Números.** Montos, precios, duraciones, fechas y contadores van en Geist Sans con cifras de ancho fijo (`.num`). Geist Mono (`.timecode`) se usa solo para los tiempos de las pantallas de edición y para los atajos de teclado.
- **Una acción principal por pantalla.** En las pantallas de proyecto la tiene la cabecera.
- **Estados.** Una etiqueta con un punto de color (`.badge.dot`), con los textos exactos de la sección 3 del mapa. Los estados de publicación usan un ícono de 12 px en lugar del punto.
- **Redes.** Un ícono de línea genérico (no el logo de la marca) dentro de un cuadrado (`.net`), siempre junto al nombre de la red.
- **Avatares.** Las personas son círculos y los workspaces, cuadrados con esquinas redondeadas.
- **Miniaturas de video.** En el diseño son un marcador de posición: un color por video, una silueta y, en los clips verticales, dos barras de subtítulos.
- **Botones deshabilitados.** Los botones con relleno deshabilitados usan `--surface-3` y `--fg-subtle`, sin transparencia, para que se lean como deshabilitados también en tema claro.
- **Nada de** degradados de fondo, emojis, bordes de color a la izquierda ni filas teñidas en forma permanente por su estado.

## Componentes del kit

| Clase | Qué es |
|---|---|
| `.topbar`, `.tabs`, `.page` | Estructura de la app: barra superior de dos filas y contenido centrado de 1200 px |
| `.proj-head`, `.proj-head.compact` | Cabecera de proyecto, igual en todas sus pestañas |
| `.subtabs` | Pestañas debajo del título de una página |
| `.btn` (`-primary`, `-accent`, `-secondary`, `-ghost`, `-danger`) | Botones de 32, 36 y 44 px |
| `.input`, `.select`, `.textarea`, `.seg`, `.switch`, `.check`, `.radio-card` | Formularios |
| `.card`, `.modal`, `.sheet`, `.pop`, `.callout` | Superficies |
| `.badge` (`.dot`, `.sm`, tonos), `.sdot`, `.progress` (`.info`, `.accent`) | Estados y progreso |
| `.table`, `.list-row` | Listas |
| `.thumb` (`-9x16`, `-16x9`, `.xs`), `.frame`, `.caps`, `.duration`, `.score` | Video |
| `.steps`, `.step` | Línea de tiempo del proceso |
| `.actionbar` | Barra fija de abajo para acciones con costo o que guardan |
| `.net` | Marca de red social |
| `.toast` | Avisos |
| `.segs` | Progreso por pasos ("Paso 2 de 4"): hecho, en curso, pendiente |
| `.clip-card`, `.clip-card.sm`, `.score-chip` | Tarjeta de clip con el puntaje sobre la miniatura |
| `.fchip` | Chip de filtro con etiqueta en plural, cantidad y estado activo |
