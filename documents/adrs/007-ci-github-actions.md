# ADR-007: Integración Continua con GitHub Actions

**Estado:** `proposed`
**Fecha:** 2026-09-20
**Par responsable:** ⚙️ Par Engine (Dev 3 + Dev 4)

---

## Contexto

La tarea `INFRA-01` del backlog solicita un pipeline de CI/CD con GitHub Actions. En esta etapa el equipo decidió implementar **únicamente Integración Continua (CI)**. El despliegue continuo, staging, publicación de imágenes, selección del proveedor de hosting, secretos de producción y rollback quedan fuera de alcance y se abordarán más adelante.

El repositorio necesita validar automáticamente cada Pull Request hacia sus ramas de integración y estabilidad. El remoto usa actualmente `dev` como rama predeterminada y también mantiene `main`; la documentación histórica todavía menciona `develop`.

Según `documents/backlog_mvp.md`, los controles mínimos de INFRA-01 son:

1. Ejecutar los checks de Django.
2. detectar migraciones faltantes;
3. ejecutar la suite automatizada;
4. construir los targets Docker `web` y `worker`;
5. impedir merges cuando un control obligatorio falla;
6. completar el pipeline en menos de cinco minutos.

La estrategia de testing del proyecto usa `pytest`, `pytest-django` y PostgreSQL. SQLite no representa correctamente los tests financieros que usan transacciones, concurrencia y `select_for_update`.

### Estado inicial y restricciones

- No existía `.github/workflows/ci.yml`.
- No existían `back/pytest.ini`, `back/conftest.py` ni `back/backend/settings/ci.py`.
- Los requirements estaban mayormente sin versiones fijadas.
- `back/backend/settings/base.py` leía `.env` incondicionalmente.
- `back/backend/settings/prod.py` no componía los settings completos de `base.py`, por lo que `check --deploy` no validaba la configuración real del proyecto.
- Los tests deben ejecutarse sin credenciales reales ni llamadas a OpenAI, Gemini, Cloudflare R2, Ayrshare u otros servicios externos.
- Los tests de concurrencia necesitan PostgreSQL 15.
- El target Docker `web` hereda actualmente el stack pesado de IA del mismo stage base que `worker`.
- El Dockerfile contenía un espacio posterior a una continuación `\` en `back/Dockerfile:19`, que impedía construir las imágenes.
- Docker Desktop no tuvo un daemon accesible en el entorno local usado para el spike; se usó Podman sobre WSL/Linux para la validación local equivalente.
- GitHub CLI no está autenticado, por lo que todavía no se inspeccionaron ni configuraron rulesets.

---

## Opciones Evaluadas

### Opción A: GitHub Actions con tests en runner y builds Docker en paralelo

El job de calidad se ejecuta directamente sobre un runner Linux con Python 3.11 y PostgreSQL 15 como service container. Los targets Docker `web` y `worker` se construyen en jobs paralelos mediante Buildx.

- **Pros:** integración nativa con el repositorio; checks visibles en Pull Requests; PostgreSQL fácil de levantar como servicio; matrices y jobs paralelos; caché de pip y BuildKit; branch protection integrada; no requiere mantener runners propios.
- **Contras:** instalación pesada de Torch/Whisper/MediaPipe; los minutos y recursos dependen del plan de GitHub; los tiempos con caché fría pueden superar el objetivo; requiere aislar correctamente servicios externos.
- **Costo:** sin infraestructura adicional dentro de la cuota disponible de GitHub Actions.

### Opción B: Ejecutar toda la suite mediante Docker Compose

GitHub Actions levantaría `db`, `redis`, `web` y `worker` usando el Compose de desarrollo y ejecutaría los tests dentro de `web`.

- **Pros:** mayor similitud superficial con el entorno local; reutiliza servicios existentes.
- **Contras:** el Compose actual depende de `back/.env`; la imagen `web` no instala las dependencias de testing; ambos targets heredan el stack pesado de IA; aumenta el tiempo y complejidad; mezcla configuración de desarrollo con CI; dificulta aislar credenciales y diagnosticar fallos.
- **Costo:** mayor consumo de minutos y almacenamiento de caché.

### Opción C: GitLab CI u otro proveedor externo

- **Pros:** runners y funcionalidades alternativas; posible mayor flexibilidad de ejecución.
- **Contras:** requiere migrar o espejar un repositorio que ya está alojado en GitHub; agrega otra plataforma, permisos y mantenimiento; branch protection y checks dejan de ser nativos.
- **Costo:** complejidad operativa adicional sin una necesidad comprobada.

### Opción D: Mantener controles manuales locales

- **Pros:** no consume minutos de CI; no requiere configuración inicial.
- **Contras:** no es reproducible; depende del entorno de cada desarrollador; no bloquea merges; no genera evidencia centralizada; permite omitir accidentalmente tests o builds.
- **Costo:** alto costo humano y mayor riesgo de regresiones.

---

## Decisión

Se propone adoptar **GitHub Actions mediante la Opción A**, con CI exclusivo para Pull Requests hacia `dev` y `main`.

La decisión permanecerá en estado `proposed` hasta contar con evidencia hosted positiva y negativa. Pasará a `accepted` cuando el pipeline final esté implementado, los checks obligatorios bloqueen un merge fallido y el presupuesto de tiempo haya sido medido.

### Evidencia local de implementación

Al 2026-09-21 se implementó el workflow y se obtuvo el siguiente baseline local:

- 38 tests recolectados y aprobados sobre PostgreSQL 15;
- suite completa en 33.80 segundos y cobertura global observada de 48%, sin imponer un umbral global;
- checks de Django y drift de migraciones aprobados;
- `check --deploy` con exit code 0 y warnings de hardening documentados;
- tests financieros aprobados en cinco procesos consecutivos;
- constraints generadas y reinstaladas en dos entornos Linux/Python 3.11 limpios, y aplicadas también a los builds Docker;
- targets `web` y `worker` construidos y ejecutados con Podman sobre Linux;
- workflow aprobado por actionlint 1.7.12;
- ausencia confirmada de publicación, login a registry y deployment.

### Evidencia hosted del PR #45

La validación se ejecutó en el PR
[`#45`](https://github.com/RomeKai/videoeditUTN/pull/45):

- happy path verde en el
  [run 35680140871](https://github.com/RomeKai/videoeditUTN/actions/runs/35680140871),
  con `Quality` en 2m44s, builds `web`/`worker` verdes y `Required CI` verde;
- test roto detectado por `pytest` y propagado a `Required CI` en el
  [run 35680356083](https://github.com/RomeKai/videoeditUTN/actions/runs/35680356083);
- drift de modelo detectado antes de pytest y propagado a `Required CI` en el
  [run 35680616239](https://github.com/RomeKai/videoeditUTN/actions/runs/35680616239);
- fallo controlado del target `worker`, con `web` y `Quality` verdes y
  `Required CI` rojo, en el
  [run 35680891478](https://github.com/RomeKai/videoeditUTN/actions/runs/35680891478);
- intento de egreso desde un proceso hijo bloqueado por timeout, sin alcanzar
  `1.1.1.1:443`, en el
  [run 35681826108](https://github.com/RomeKai/videoeditUTN/actions/runs/35681826108);
- ejecución limpia sin caché de pip ni BuildKit verde en el
  [run 35682124222](https://github.com/RomeKai/videoeditUTN/actions/runs/35682124222),
  con `Quality` en 2m53s, `web` en 2m09s y `worker` en 2m33s.

El workflow completo cumple el presupuesto de cinco minutos tanto sin caché
como con caché caliente. Todos los cambios deliberadamente rotos fueron
retirados. El ADR permanece `proposed` únicamente porque branch protection no
fue configurada ni verificada dentro de este alcance autorizado.

### Arquitectura del pipeline

1. **Job `quality`** sobre `ubuntu-24.04`:
   - Python 3.11.
   - PostgreSQL 15 Alpine como service container.
   - Dependencias de sistema mínimas requeridas por el backend.
   - Instalación explícita de PyTorch CPU antes del stack IA.
   - Dependencias reproducibles mediante constraints validadas en Linux.
   - `python -m pip check`.
   - `python manage.py check`.
   - `python manage.py check --deploy --settings=backend.settings.prod`.
   - `python manage.py makemigrations --check --dry-run`.
   - `python -m pytest` con reportes de cobertura y JUnit.

2. **Job `docker-build`** en paralelo:
   - matriz para targets `web` y `worker`;
   - Buildx;
   - caché GHA separada por target;
   - `push: false`;
   - sin login a registries ni secretos de publicación.

3. **Job agregado `Required CI`**:
   - depende de `quality` y `docker-build`;
   - falla si cualquiera de los jobs requeridos falla, se cancela o se omite;
   - constituye el único nombre estable exigido por branch protection.

4. **Políticas generales**:
   - permisos mínimos: `contents: read`;
   - cancelación de runs anteriores para el mismo PR;
   - sin filtros de paths, para evitar checks requeridos ausentes en PRs de documentación;
   - sin secretos reales ni acceso a APIs pagas;
   - sin publicación de imágenes;
   - sin jobs de deployment.

### Alcance explícitamente excluido

Este ADR no decide ni implementa:

- proveedor de staging o producción;
- deployment automático o manual;
- publicación en un container registry;
- credenciales de cloud;
- estrategia de rollback;
- aprobaciones de ambientes;
- Fly.io, Railway, Render, VPS u otro destino.

Estas decisiones permanecen en `ADR-006` y deberán resolverse en un trabajo posterior de CD.

### Criterios para aceptar este ADR

- Un PR limpio ejecuta y pasa todos los checks sin secretos de producción.
- Un test roto hace fallar `quality` y `Required CI`.
- Un cambio de modelo sin migración hace fallar el control de drift.
- Un Dockerfile roto hace fallar `docker-build` y `Required CI`.
- Una llamada externa no mockeada falla sin alcanzar al proveedor.
- Los targets `web` y `worker` construyen correctamente.
- Branch protection impide el merge cuando `Required CI` está rojo.
- Se registran tiempos con caché fría y caliente.
- El workflow completo cumple el objetivo de menos de cinco minutos bajo las condiciones documentadas; si solo lo cumple con caché caliente, el resultado será `partial`.
- Todos los cambios deliberadamente rotos se retiran y existe un último run completamente verde.

---

## Consecuencias

### Positivas

- Los Pull Requests reciben feedback reproducible antes del merge.
- Los tests financieros se ejecutan sobre PostgreSQL real.
- Se detectan migraciones faltantes y Dockerfiles rotos.
- El check estable `Required CI` desacopla branch protection de la estructura interna del workflow.
- CI no depende de secretos ni servicios externos del producto.
- CI y CD quedan separados, permitiendo avanzar sin decidir todavía la infraestructura de producción.

### Negativas

- El stack de IA hace costosa la instalación y puede impedir el objetivo de cinco minutos.
- Será necesario mantener settings, fixtures y constraints específicos de CI.
- Construir dos targets que comparten una base pesada consume tiempo y caché.
- La validación completa exige publicar una rama y crear un PR de prueba en GitHub.

### Riesgos

- **Dependencias no fijadas:** dos runs del mismo commit podrían resolver versiones distintas. Mitigación: generar constraints desde Linux/Python 3.11 y verificarlas en un segundo entorno limpio.
- **Descarga de CUDA:** `openai-whisper` puede resolver Torch desde PyPI. Mitigación: instalar y fijar explícitamente PyTorch CPU.
- **Llamadas externas accidentales:** un mock incompleto puede consumir una API real. Mitigación: bloqueo de red temprano más mocks explícitos en los límites.
- **Falsos verdes de `check --deploy`:** los settings de producción están incompletos. Mitigación: corregir primero su composición y registrar warnings de hardening sin afirmar que CD está listo.
- **Tests manuales recolectados por pytest:** existen scripts visuales bajo `back/tests/unit`. Mitigación: moverlos o renombrarlos y definir explícitamente la suite automatizada.
- **Branch protection no verificable:** puede faltar permiso o funcionalidad del plan de GitHub. Mitigación: mantener el resultado como `partial` hasta observar un merge bloqueado.

---

## Referencias

- [Backlog MVP — INFRA-01](../backlog_mvp.md#infra-01-cicd--github-actions-p1--missing)
- [Backlog MVP — estrategia de testing](../backlog_mvp.md#estrategia-de-testing)
- [Backlog MVP — CORE-06](../backlog_mvp.md#core-06-tests-de-integración-del-pipeline-p0--partial)

- [ADR-006: Estrategia de Deployment y CI/CD](006-deployment-strategy.md)
- [Dockerfile actual](../../back/Dockerfile)
- [Requirements de desarrollo](../../back/requirements/dev.txt)
- [Requirements de IA](../../back/requirements/ia.txt)
- [Settings base](../../back/backend/settings/base.py)
- [Settings de producción](../../back/backend/settings/prod.py)
- [Tests de integridad financiera](../../back/apps/core/tests/test_critical_integrity.py)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [GitHub: About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [GitHub: PostgreSQL service containers](https://docs.github.com/en/actions/use-cases-and-examples/using-containerized-services/creating-postgresql-service-containers)
- [Docker Build Push Action](https://github.com/docker/build-push-action)
- [Django deployment checklist](https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/)
