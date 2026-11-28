# Documentación de OneCreator

Qué hay en esta carpeta y por dónde empezar.

## Si venís por la demo

1. [Runbook de la demo](runbooks/demo.md): levantar, sembrar, el recorrido, la lista de antes de presentar y qué hacer si algo falla.
2. [Mapa de pantallas](producto/mapa-de-pantallas.md): qué es cada pantalla que vas a mostrar.

## Qué hay

| Carpeta o archivo | Qué es | Empezá por |
|---|---|---|
| [`runbooks/`](runbooks/) | Pasos para operar: la demo, las redes, el piloto de IA | La tabla de abajo |
| [`ia/`](ia/README.md) | Cómo funciona el backend, flujo por flujo: crear un proyecto, la ingesta, la transcripción y la selección, Prompt-to-Edit, publicar, el render, y la capacidad, los costos y la operación | [`ia/README.md`](ia/README.md) |
| [`producto/`](producto/mapa-de-pantallas.md) | El mapa de pantallas (roles, planes, errores y los huecos de backend) y el [sistema visual](producto/diseno/README.md) | [`producto/mapa-de-pantallas.md`](producto/mapa-de-pantallas.md) |
| [`adrs/`](adrs/README.md) | Las decisiones de arquitectura y su porqué | [`adrs/README.md`](adrs/README.md) |
| [`integracion/`](integracion/guia-conexion.md) | Cómo se conecta la web con el backend: el contrato, las reglas de cada endpoint y la lista para dar un área por conectada | [`integracion/guia-conexion.md`](integracion/guia-conexion.md) |
| [`backlog_mvp.md`](backlog_mvp.md) | El backlog del MVP, por ticket | — |
| [`entregas/`](entregas/tp1.md) | Entregas de la materia | — |
| `mapa.txt` | Una lista vieja de archivos del backend. No se mantiene | — |

## Runbooks

| Runbook | Para qué |
|---|---|
| [`demo.md`](runbooks/demo.md) | La demo del producto en una máquina, con Docker: backend real, la web en modo híbrido y datos sembrados |
| [`redes-sociales-prueba.md`](runbooks/redes-sociales-prueba.md) | Probar conectar redes y publicar con una key real de Ayrshare, o contra un Ayrshare falso |
| [`social-app-review.md`](runbooks/social-app-review.md) | Registrar las apps en TikTok y Meta y seguir su revisión |
| [`ai-core-pilot-runbook.md`](runbooks/ai-core-pilot-runbook.md) | El piloto de AI Core V2: qué se mide, los gates y el rollback |

## Dónde está cada tema

| Busco… | Está en |
|---|---|
| Las variables de entorno y qué hace cada una | [`back/.env.example`](../back/.env.example) y [la configuración en `ia/07`](ia/07-capacidad-costos-y-operacion.md#configuración) |
| Qué significa un evento de log, o qué mirar si algo anda mal | [`ia/07`, «Operación: eventos de log»](ia/07-capacidad-costos-y-operacion.md#operación-eventos-de-log) |
| Conectar redes y publicar: estados, errores y reintentos | [`ia/05`](ia/05-seo-y-publicacion.md) y el [runbook de redes](runbooks/redes-sociales-prueba.md) |
| El modo mantenimiento | [Runbook de la demo, «Si algo falla»](runbooks/demo.md#6-si-algo-falla) y `MAINTENANCE_MODE` en [`ia/07`](ia/07-capacidad-costos-y-operacion.md#otros-settings-de-la-plataforma) |
| El contrato de la API, endpoint por endpoint | [`front/src/api/CONTRACT.md`](../front/src/api/CONTRACT.md) (generado) |
| Qué ruta tiene cada pantalla | [`front/ROUTES.md`](../front/ROUTES.md) (generado) |
| Correr la web: modos mock, real e híbrido, y sus tests | [`front/README.md`](../front/README.md) |
| Correr el backend y sus tests | [`back/README.md`](../back/README.md) |
| Cómo se trabaja en el repo: ramas, commits, PRs | [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| El registro de uso de IA de la materia | [`AI-DECISIONS.md`](../AI-DECISIONS.md) |

## Cómo se escriben

- En español rioplatense, con oraciones cortas.
- Los nombres (settings, tareas, endpoints, archivos) se copian del código, sin números de línea.
- Lo que no se pudo probar se dice: «sin probar».
- `CONTRACT.md` y `ROUTES.md` no se editan a mano: se regeneran con `npm run contract:doc` en `front/`.
