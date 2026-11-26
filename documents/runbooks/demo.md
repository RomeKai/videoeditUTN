# Runbook: la demo del producto

> **Para:** quien presenta y quien prepara la máquina.
> **Verificado:** 2026-11-26, en una máquina de 4 vCPU y 15 GB. Qué se probó y qué no está en [la última sección](#qué-se-verificó-y-qué-no).
> **Qué levanta:** Postgres, Valkey, la API de Django, un worker y un beat de Celery, la web en modo híbrido y un proxy. Todo queda en **http://localhost:8080**.

No es producción: usa `runserver`, `DEBUG=True` y archivos en disco. Corre en la máquina desde la que se presenta.

## 1. Requisitos

- Docker con Compose 2.x, 8 GB de RAM libres y unos 10 GB de disco (la imagen del backend trae PyTorch).
- Chrome, abriendo siempre `http://localhost:8080`. Los mocks son un service worker: con otra IP u otro nombre por HTTP no arrancan.
- Un video de prueba **en un archivo**: 1 a 3 minutos, con alguien hablando claro en español.
- `back/.env`, copiado de `back/.env.example`. Nunca se sube al repo. Lo que hay que completar:

| Variable | Para qué | ¿Hace falta? |
|---|---|---|
| `OPENAI_API_KEY` | Moderación del texto. Falla cerrado: sin la key, todo proyecto nuevo termina en error | Sí, para procesar un video en vivo |
| `GEMINI_API_KEY` | Elegir los clips, leer el prompt («Crear con IA») y el texto de las publicaciones | Sí, para procesar un video en vivo |
| `GROQ_API_KEY` | Transcripción remota | Solo con `AI_CORE_V2_ENABLED=True` |
| `AI_CORE_V2_ENABLED` | `True`: transcribe Groq, en segundos. `False` (como viene): Whisper local en CPU, lento | A decidir (ver [pendientes](#7-pendientes-del-equipo-antes-del-lunes)) |
| `USE_R2` | Dejalo en `False`: los archivos van a `back/media` y los sirve el proxy | — |
| `AYRSHARE_API_KEY`, `STRIPE_*`, `XAI_API_KEY` | No se usan en la demo: publicar y pagar son mocks | No |

`DEBUG=True` y el resto de `back/.env.example` sirven como están. **Sin ninguna key** igual se puede mostrar todo menos el análisis en vivo: los proyectos de ejemplo y el render real no llaman a ningún proveedor.

## 2. Levantar y sembrar

```bash
cp back/.env.example back/.env        # y completá las keys
alias demo='docker compose -f docker-compose.yml -f docker-compose.demo.yml'

demo up -d --build                    # la primera vez tarda: hacelo el día anterior
demo ps                               # web tiene que quedar "healthy"
demo exec web python manage.py seed_demo --with-projects
```

`seed_demo` imprime el usuario y la contraseña al final:

- usuario `demo@onecreator.local`, contraseña `OneCreator-Demo-2026`;
- workspace «Demo OneCreator» en el plan Pro, con 4.860 tokens y el brand kit «Kit de la demo»;
- con `--with-projects`, dos proyectos **de ejemplo**, marcados `[DEMO]`: uno listo para revisar y otro con clips listos. Son datos de prueba: un patrón de colores generado con ffmpeg y un texto escrito a mano. La IA no los analizó, y así lo dicen el título y el «Por qué» de cada clip.

Se puede correr las veces que haga falta: no duplica nada, repone el plan, el saldo y la contraseña, y no toca ninguna otra cuenta. `--reset-projects` borra los dos proyectos de ejemplo y los crea de nuevo (por ejemplo, después de aprobar el de revisión en un ensayo). Sin `DEBUG=True` no corre, salvo con `--force`.

Para apagar: `demo down` (los datos quedan). `demo down -v` borra la base.

## 3. Qué es real y qué es mock

La web corre en modo híbrido. **Hoy** la demo manda al backend real el usuario, los proyectos, los clips y los brand kits, y deja **enteros en mocks** los planes, la billetera, las redes, las publicaciones, los miembros y las notificaciones. La lista se cambia en un solo lugar: `DEMO_API_REAL` (`docker-compose.demo.yml` la pasa a `NEXT_PUBLIC_API_REAL` al construir `front`).

> **Por qué los planes y las publicaciones son mocks hoy.** El backend ya tiene `billing.plans`, `social.posts` y `social.post`, pero no la suscripción ni crear una publicación. Con esos tres en real, «tu plan» no coincide con ningún plan de la lista y una publicación creada en la demo no aparece en el calendario. Quedan en mocks hasta que estén los endpoints de suscripción.
>
> **La lista se va a ampliar.** Hay PRs en curso para billetera, movimientos y precios, para suscripción y compras, y para workspaces y miembros. Cuando se mergeen, se suman sus dominios (`billing`, `workspaces`) al valor por defecto de `DEMO_API_REAL` en `docker-compose.demo.yml`, se reconstruye con `demo up -d --build front` y se actualiza la tabla de abajo. Un dominio en la lista va entero al backend, también lo que todavía no existe ahí (responde 404): si falta algo, nombrá los endpoints uno por uno. `DEMO_API_REAL=existing` manda al backend todo lo que el contrato marca como existente.

| Pantalla | Ruta | Quién responde |
|---|---|---|
| Iniciar sesión | `/login` | **Backend** |
| Proyectos | `/proyectos` | **Backend** |
| Nuevo proyecto | `/proyectos/nuevo` | **Backend** crea, valida y cobra. El costo estimado y los límites del plan que se ven antes son mock |
| Proyecto: resumen, transcripción, actividad | `/proyectos/:id` | **Backend** |
| Revisión de clips | `/proyectos/:id/revision` | **Backend**, también el video y el render |
| Clips, editor de clip y descargas | `/proyectos/:id/clips` | **Backend**. La miniatura de cada clip es un dibujo: todavía no existe en el backend |
| Brand kits | `/brand-kits` | **Backend** (duplicar es mock) |
| Mi cuenta y avisos por email | menú del usuario | **Backend** |
| Saldo de la barra, Uso y facturación, Planes, Comprar tokens | `/uso` | Mock |
| Redes sociales | `/ajustes/redes` | Mock |
| Publicar y Publicaciones | panel «Publicar», `/publicaciones` | Mock |
| Miembros y ajustes del workspace | `/ajustes` | Mock |
| Notificaciones (la campana) | — | Mock |
| Crear cuenta y recuperar contraseña | `/registro`, `/recuperar` | No existen en el backend: **no las muestres** |

Dos cosas para tener presentes:

- **El saldo que se ve es el del mock.** Arranca en 4.860, igual que el real, pero no baja cuando el backend cobra. El saldo real está en `http://localhost:8080/admin/` o en `GET /api/v1/me/`.
- El botón del matraz (borde izquierdo) muestra qué dominio es real y cuál es mock. Cada respuesta de un mock lleva la cabecera `X-OC-Mock`.

## 4. El recorrido

1. **Iniciar sesión** con el usuario de la demo. Entra a **Proyectos**, con los proyectos reales.
2. **Nuevo proyecto**: subí el archivo de prueba, elegí formato y subtítulos, y creá. Mostrá el proyecto en «Procesando».
3. Mientras procesa, abrí el proyecto **listo para revisar** que dejaste preparado.
4. **Revisión**: el video, la transcripción y los clips sugeridos. Elegí dos y tocá «Renderizar». Es el render de verdad.
5. **Clips**: aparecen en «Listo» cuando terminan. Descargá uno y abrilo.
6. **Brand kits**: abrí «Kit de la demo» y cambiá un color. Queda guardado en el backend.
7. **Publicar** un clip y mirar **Publicaciones**. Decilo: en esta demo la publicación es simulada.
8. **Uso y facturación** y **Planes**. También simulados.
9. Volvé al proyecto del paso 2. Si ya está listo para revisar, mostralo.

## 5. Antes de la presentación

El día anterior, con las keys puestas:

1. `demo up -d --build` y `seed_demo --with-projects`.
2. **Procesá de verdad el video de prueba**, de punta a punta: subir, revisar, renderizar. Dejá ese proyecto terminado. Es el mejor respaldo, porque es un resultado real de la IA.
3. Subí el mismo video otra vez y dejalo **sin aprobar**, en «Listo para revisar», para el paso 3 del recorrido.
4. En el Chrome de la presentación, conectá una red en `/ajustes/redes` (es un mock y queda guardado en ese navegador).

El mismo día:

5. `demo ps` y entrá a la web. Renderizá un clip cualquiera: la primera tarea de un worker recién levantado es la más lenta.
6. Si aprobaste el proyecto de ejemplo en un ensayo: `seed_demo --reset-projects`. No lo corras con un render en curso.

Cuánto tarda cada cosa en CPU:

| Paso | Tiempo | De dónde sale |
|---|---|---|
| `seed_demo --with-projects` | 35 s | Medido |
| Armar el proxy de un video de 90 s en 720p | 18 s | Medido |
| Render de un clip de 20 s (720p, relleno, con subtítulos, sin seguir caras) | 9 a 19 s, con dos a la vez | Medido |
| Render con seguimiento de caras, 1080p o layout «Speaker dinámico» | Hasta 4 veces la duración del clip | `RENDER_CLIP_SECONDS_PER_SECOND` |
| Transcripción con Groq (`AI_CORE_V2_ENABLED=True`) | Segundos | ADR-002 (addendum), sin medir acá |
| Transcripción con Whisper local (`False`) | 5 a 7 min por cada 10 min de video. La primera vez cada proceso del worker baja el modelo (139 MB) | ADR-002 (addendum); la descarga, medida |
| Elegir los clips (Gemini) | Hasta 60 s por llamada | `AI_LLM_TIMEOUT_SECONDS`, sin medir acá |
| Construir las imágenes la primera vez | Varios minutos, según la conexión | Sin medir |

Regla práctica: con Groq, un video de 2 minutos está listo para revisar en alrededor de un minuto. Con Whisper local no lo proceses en vivo: usá lo que dejaste preparado.

## 6. Riesgos y qué hacer

| Riesgo | Qué hacer |
|---|---|
| **Links de YouTube o Vimeo.** Esos sitios bloquean las descargas desde IPs de datacenter, y a veces también desde otras | Usá siempre **subir archivo**. No muestres «pegar un link» |
| **Publicar en redes** necesita una cuenta y una key de Ayrshare | Es mock. Decilo en voz alta. Nada sale a ninguna red |
| **Pagos**: Stripe no está integrado | Comprar tokens y cambiar de plan son mocks (`/dev/checkout` hace de pasarela) |
| **Un proveedor de IA falla o tarda** (key vencida, sin cuota, sin internet) | El proyecto queda en error y los tokens vuelven solos. Seguí con el proyecto preparado. Probá las keys el mismo día |
| **El video no tiene voz** | El proyecto falla con «no se detectó voz». Usá un video con alguien hablando |
| **La web dice «No pudimos conectar»** | `demo ps`: si `web` no está, `demo logs web --tail 50` y `demo up -d` |
| **Se abrió la web con otra dirección** | Los mocks no arrancan fuera de `localhost`. Volvé a `http://localhost:8080` |
| **Cambiaste `back/.env`** | `demo up -d --force-recreate web worker beat`. Lo que cambia en `docker-compose.demo.yml` para `front` pide `--build` |

**Si se cae el worker** (un proyecto no sale de «Procesando» o los clips no salen de «Renderizando»):

1. `demo ps` y `demo logs worker --tail 50`. Si el contenedor murió, Docker lo levanta solo (`restart: on-failure`); si no, `demo restart worker`.
2. **No esperes la tarea que estaba corriendo.** Celery la vuelve a entregar recién a las 4 horas (`CELERY_VISIBILITY_TIMEOUT`), y `recover_stuck_renders` actúa después de más de dos horas. Lo que estaba en cola sin empezar sí corre apenas vuelve el worker.
3. Seguí con el proyecto que dejaste preparado, o con los `[DEMO]`.
4. Para el video en vivo, **creá el proyecto de nuevo**. La web todavía no tiene el botón para retomar un proyecto en error (el endpoint `projects/:id/redispatch/` existe, pero ninguna pantalla lo llama). Un clip en error sí tiene «Reintentar».
5. Si el worker muere por memoria, bajá la concurrencia: `DEMO_WORKER_CONCURRENCY=1 demo up -d worker` (por defecto son 2).

## 7. Pendientes del equipo antes del lunes

- [ ] **Keys reales** de OpenAI y Gemini, y de Groq si se usa V2, cargadas en `back/.env` de la máquina de la demo. Con cuota para los ensayos.
- [ ] **Decidir el pipeline:** `AI_CORE_V2_ENABLED=True` (Groq, rápido) o `False` (Whisper local, lento). El piloto de V2 todavía no pasó su gate G0 ([runbook del piloto](ai-core-pilot-runbook.md), §11.1): probá el video de la demo con el que elijan.
- [ ] **Elegir el video de prueba** y ensayarlo de punta a punta en la máquina de la presentación.
- [ ] **Decidir qué se dice** sobre lo simulado: pagos, redes, publicaciones, miembros y notificaciones.
- [ ] **`back/docker/entrypoint.sh`** sigue sin dueño. No frena la demo, porque ni el `Dockerfile` ni los compose lo usan (las migraciones las corre `docker-compose.demo.yml`). Si alguien lo activa hoy, no arranca: espera `POSTGRES_HOST` y `POSTGRES_PORT`, que ningún compose define, y su última línea deja el contenedor vivo aunque Django se caiga.
- [ ] **El video de la revisión sin el proxy.** Con `DEBUG=True` y `USE_R2=False` Django ya sirve `/media/`, así que las descargas de clips andan. Pero `proxy_url` llega como ruta relativa (`/media/...`) y el navegador la pide al origen de la web, no al de la API: sin un mismo origen (el proxy de la demo) el video de la revisión no carga. Falta que el backend la devuelva absoluta, o un rewrite en la web.

## Sin Docker

Sirve para desarrollar, no para presentar. Con `DEBUG=True` y `USE_R2=False`, Django sirve los archivos de `back/media` en `/media/`, y las descargas de clips andan. Dos límites: el servidor de desarrollo no responde pedidos por rango, así que adelantar o retroceder un video puede no funcionar; y el video de la revisión necesita que la web y la API estén en el mismo origen (último punto de arriba).

```bash
# Postgres y Redis locales; después, en back/, con DEBUG=True, USE_R2=False, DATABASE_URL,
# REDIS_URL y CACHE_REDIS_URL (otra base de Redis) en el entorno o en back/.env:
export DJANGO_SETTINGS_MODULE=backend.settings.dev
python manage.py migrate && python manage.py seed_demo --with-projects
python manage.py runserver 8000
celery -A backend worker -l info -Q celery,render -c 2
celery -A backend beat -l info --schedule=/tmp/celerybeat-schedule

# En front/: el modo queda fijo al construir
NEXT_PUBLIC_API_MODE=hybrid API_MOCKS_IN_BUILD=true NEXT_PUBLIC_API_URL=http://localhost:8000 npm run build
npx next start
```

## Qué se verificó y qué no

Verificado el 2026-11-26:

- `docker compose -f docker-compose.yml -f docker-compose.demo.yml config` es válido, y el servicio `redis` levanta y queda `healthy`.
- El backend con `runserver`, un worker y un beat de Celery contra Postgres y Redis locales: migraciones, `seed_demo`, login, proyectos, brand kits, aprobar la revisión y el render real de los clips.
- La web construida en modo híbrido con la lista de la demo, detrás del nginx de `docker/demo/nginx.conf` (en un contenedor): login, las pantallas de la tabla, el video de la revisión con pedidos por rango, aprobar y renderizar desde la pantalla, conectar una red simulada, abrir «Publicar» con un clip real y una subida de 32 MB. Sin errores de consola ni respuestas 4xx o 5xx.
- La imagen de `front/Dockerfile` construye y sirve la web.
- Django sirve `/media/` con `DEBUG=True` y `USE_R2=False`, y no lo hace con `DEBUG` apagado ni con R2 (tests en `apps/core/tests/test_media_serving.py`).

Sin verificar:

- **La imagen del backend y el `up` completo en Docker.** La imagen no se construyó (pesa varios GB) y Docker Hub cortó la descarga de Postgres por límite de pedidos. Los servicios se probaron por separado, como dice arriba. **Hacé un `demo up -d --build` completo antes del lunes.**
- **El análisis con IA en vivo** (transcripción con voz, moderación y selección de clips): no había keys. Un video sin voz falla donde tiene que fallar y devuelve los tokens.
- **Confirmar una publicación simulada** hasta el final, y el paso por Uso y Planes más allá de abrir las pantallas.
- **Links de YouTube y Vimeo**, Ayrshare y Stripe.
