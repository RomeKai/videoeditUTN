# Runbook: la demo del producto

> **Para:** quien presenta y quien prepara la máquina.
> **Verificado:** 2026-11-26, en una máquina de 4 vCPU y 15 GB. Qué se probó y qué no está en [la última sección](#qué-se-verificó-y-qué-no).
> **Qué levanta:** Postgres, Valkey, la API de Django, un worker y un beat de Celery, la web en modo híbrido y un proxy. Todo queda en **http://localhost:8080**.

No es producción: usa `runserver`, `DEBUG=True`, una cuenta de contraseña conocida y archivos en disco que se sirven sin sesión. Corre en la máquina desde la que se presenta, y **solo se llega desde esa máquina**: el proxy (8080) y la API (8001) se publican en `127.0.0.1`, nada en la red.

**Es HTTP en `localhost` y nada más.** No la pongas detrás de HTTPS ni de un túnel: los links de los videos locales saldrían con `http://` y el navegador los bloquearía por contenido mixto.

## 1. Requisitos

- Docker con **Compose 2.24 o más nuevo** (`docker compose version`; `docker-compose.demo.yml` usa `!override`), 8 GB de RAM libres y unos 10 GB de disco (la imagen del backend trae PyTorch).
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

**Cambiá `SECRET_KEY`**: no dejes la del ejemplo, que es pública y firma las sesiones. Una nueva sale de `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`. `DEBUG=True` y el resto de `back/.env.example` sirven como están. **Sin ninguna key** igual se puede mostrar todo menos el análisis en vivo: los proyectos de ejemplo y el render real no llaman a ningún proveedor.

## 2. Levantar y sembrar

```bash
cp back/.env.example back/.env        # completá las keys y cambiá SECRET_KEY
alias demo='docker compose -f docker-compose.yml -f docker-compose.demo.yml'

demo up -d --build                    # la primera vez tarda: hacelo el día anterior
demo ps                               # web tiene que quedar "healthy"
demo exec web python manage.py seed_demo --with-projects
```

`seed_demo` imprime el usuario y la contraseña al final:

- usuario `demo@onecreator.local`, contraseña `OneCreator-Demo-2026`, de nombre «Ana Demo» (no el del workspace, para que una invitación diga «Ana Demo te invitó a Demo OneCreator»). Si la base ya tenía el usuario con el nombre viejo, «Demo OneCreator», el comando lo renombra; un nombre que alguien editó en Perfil se conserva;
- workspace «Demo OneCreator» en el plan Pro, con 4.860 tokens y el brand kit «Kit de la demo». Pro queda como cortesía, igual que un plan puesto en el admin: sin período pagado, así nunca vence ni baja solo a Gratis;
- con `--with-projects`, dos proyectos **de ejemplo**, marcados `[DEMO]`: uno listo para revisar y otro con clips listos. Son datos de prueba: un patrón de colores generado con ffmpeg y un texto escrito a mano. La IA no los analizó, y así lo dicen el título y el «Por qué» de cada clip. Cada uno trae los tiempos de sus etapas (números de ejemplo): por eso el proyecto muestra Inicio y Duración, y Nuevo proyecto ya da un tiempo estimado sin haber procesado un video real. El estimado lee los proyectos de los últimos 30 días: pasado ese plazo, `--reset-projects` los crea de nuevo.

Se puede correr las veces que haga falta: no duplica nada, repone el plan (sin el período ni la bajada que haya dejado un ensayo), el saldo y la contraseña, y no toca ninguna otra cuenta. `--reset-projects` borra los dos proyectos de ejemplo y los crea de nuevo (por ejemplo, después de aprobar el de revisión en un ensayo). Sin `DEBUG=True` no corre, salvo con `--force`.

Para apagar: `demo down` (los datos quedan). `demo down -v` borra la base.

## 3. Qué es real y qué es mock

La web corre en modo híbrido. **Hoy** la demo manda al backend real el usuario, los proyectos, los clips, los brand kits, la billetera (saldo, consumo, movimientos y su exportación), los precios (fórmula y estimado), los planes, la suscripción, comprar tokens y el checkout de prueba, y los workspaces con sus miembros (crear, renombrar, transferir y borrar un workspace; ver los miembros, cambiar un rol, quitar a alguien, e invitar por email, reenviar y aceptar la invitación), y las notificaciones de la campana. Deja **en mocks** las redes y las publicaciones. La lista se cambia en un solo lugar: `DEMO_API_REAL` (`docker-compose.demo.yml` la pasa a `NEXT_PUBLIC_API_REAL` al construir `front`).

> **Los pagos son simulados.** No hay pasarela de pagos (Stripe es PAY-02). El backend de la demo corre con `DEBUG=True`, y `settings/dev.py` trae `PAYMENTS_PROVIDER=simulated` por defecto: comprar tokens o pasar a otro plan pago crea el pago de verdad en el backend, y el «checkout» es la página `/dev/checkout` de la web, que lo aprueba o lo rechaza sin cobrar nada (`POST /api/v1/token-purchases/<id>/simulate/`). Al aprobar, el backend acredita los tokens (un movimiento «Compra») o cambia el plan. No pongas `PAYMENTS_PROVIDER=` vacío en `back/.env`: sin proveedor, comprar responde «Los pagos todavía no están habilitados».
>
> **Por qué las publicaciones siguen en mocks.** El backend ya tiene las publicaciones enteras: listar, ver, crear (también en lote y como borrador), editar, cancelar, reintentar y regenerar el texto con IA (`social.posts`, `social.post`, `social.createPost`, `social.createPostsBulk`, `social.updatePost`, `social.cancelPost`, `social.retryPost` y `social.regeneratePostSeo`). Lo que todavía no tiene es conectar las cuentas de las redes (`social.accounts`, `social.connect` y `social.disconnect`, hueco 10): sin la cuenta de Ayrshare del espacio, publicar responde 409 `account_not_connected`. Mezclar una lista real de publicaciones con cuentas conectadas de mentira no cierra (el panel mostraría redes conectadas que el backend no conoce), así que la demo deja todo Publicar en mocks, y `DEMO_API_REAL` no nombra ningún endpoint `social.*`, hasta que exista conectar cuentas. Ojo con `DEMO_API_REAL=existing`: manda al backend lo que el contrato marca como existente, y eso ahora incluye las publicaciones pero no las cuentas. Para mostrar Publicar, usá la lista por defecto.
>
> **La campana ya trae ejemplos.** `seed_demo --with-projects` deja cuatro notificaciones del usuario de la demo, con los mismos textos que escriben los eventos reales: el proyecto de ejemplo listo para revisar y un proyecto que falló sin cobrar nada (sin leer), el render terminado del proyecto con clips y el saldo de la demo acreditado (leídas). La del proyecto que falló es solo un aviso de ejemplo: no hay tal proyecto y lleva a Proyectos. Las demás notificaciones se escriben cuando pasa el evento: al crear un proyecto de verdad o aprobar una compra en el checkout de prueba aparecen solas en menos de un minuto. Volver a correr `seed_demo` no las duplica ni cambia cuáles están leídas.
>
> **Cómo se cambia la lista.** Un dominio en `DEMO_API_REAL` va entero al backend, también lo que todavía no existe ahí (responde 404): si falta algo, nombrá los endpoints uno por uno, como en el valor por defecto. En un dominio nombrado así tienen que estar todos los endpoints que el backend ya tiene: uno que falte queda en mocks y la pantalla mezcla una respuesta simulada con datos reales (así quedó «Seguir con» en Planes en el ensayo 3). Lo comprueba `front/src/api/demo-list.test.ts`. Después de cambiarla, `demo up -d --build front` y actualizá la tabla de abajo. `DEMO_API_REAL=existing` manda al backend todo lo que el contrato marca como existente.

| Pantalla | Ruta | Quién responde |
|---|---|---|
| Iniciar sesión | `/login` | **Backend** |
| Proyectos | `/proyectos` | **Backend** |
| Nuevo proyecto | `/proyectos/nuevo` | **Backend** crea, valida, estima y cobra. Los límites del plan que se ven antes son mock |
| Proyecto: resumen, transcripción, actividad | `/proyectos/:id` | **Backend** |
| Revisión de clips | `/proyectos/:id/revision` | **Backend**, también el video y el render |
| Clips, editor de clip y descargas | `/proyectos/:id/clips` | **Backend**. La miniatura de cada clip es un dibujo: todavía no existe en el backend |
| Brand kits | `/brand-kits` | **Backend**, también duplicar (copia los archivos) |
| Mi cuenta y avisos por email | menú del usuario | **Backend** |
| Saldo de la barra, Uso (consumo y movimientos) | `/uso` | **Backend** |
| Planes y Comprar tokens | `/uso/planes`, `/uso/comprar` | **Backend**, con el proveedor simulado: no se cobra nada |
| Checkout de prueba | `/dev/checkout` | **Backend**: aprueba o rechaza el pago simulado |
| Redes sociales | `/ajustes/redes` | Mock |
| Publicar y Publicaciones | panel «Publicar», `/publicaciones` | Mock. El backend ya las tiene, pero no las cuentas de las redes: quedan en mocks hasta que estén (ver arriba) |
| Ajustes del workspace, Miembros y el selector de workspace | `/ajustes`, `/ajustes/miembros`, barra superior | **Backend**: renombrar, transferir y borrar el workspace, crear otro y cambiar entre ellos, la lista de miembros, cambiar un rol, quitar a alguien, **Invitar** y **Reenviar**. El invitado aparece como «Invitación pendiente» y ocupa un lugar del plan |
| Aceptar una invitación | `/invitacion#token=…` | **Backend**. El email sale por consola: buscá el link `/invitacion#token=…` en `demo logs worker`, copialo entero (el token va después de `#`) y abrilo en otro navegador o en una ventana privada (sin la sesión del usuario de la demo). Con un email sin cuenta, la crea ahí mismo y entra al workspace. El link sirve una vez y «Reenviar» lo cambia por otro. El plan Gratis tiene 2 lugares: para invitar a más de una persona, el workspace tiene que estar en un plan más grande |
| Notificaciones (la campana) | barra superior | **Backend**: avisa cuando un proyecto queda listo para revisar, cuando termina el render, cuando un proyecto falla (con lo devuelto), cuando el saldo queda bajo (al dueño y a los admins) y cuando un pago se aprueba o se rechaza. Se actualiza sola cada minuto y al volver a la pestaña. Los emails de esos avisos (menos los de pagos) salen por consola: miralos con `demo logs worker`. Cada persona los apaga por tipo en Perfil › Notificaciones. No avisa de publicaciones fallidas |
| Crear cuenta, verificar el email y recuperar contraseña | `/registro`, `/verificar`, `/recuperar` | **Backend**. Los emails salen por consola (settings `dev`): el link del registro en `demo logs web`, los de reenviar y recuperar en `demo logs worker`. Copialo de ahí al navegador donde creaste la cuenta: en otro, el link pide iniciar sesión y verifica después. Los links de verificar y de recuperar llevan el token después de `#`: copialos enteros. Sin verificar en la demo: probalo antes |

Dos cosas para tener presentes:

- **El saldo que se ve es el real** (`GET /api/v1/wallet/`): baja cuando el backend cobra y vuelve cuando devuelve. Para ver los movimientos uno por uno, Uso → Movimientos, o el admin de Django (`http://localhost:8080/admin/`) si creaste un usuario para entrar: `demo exec web python manage.py createsuperuser`. El usuario de la demo no entra al admin.
- El botón del matraz (borde izquierdo) muestra qué dominio es real y cuál es mock. Cada respuesta de un mock lleva la cabecera `X-OC-Mock`.

## 4. El recorrido

1. **Iniciar sesión** con el usuario de la demo. Entra a **Proyectos**, con los proyectos reales.
2. **Nuevo proyecto**: subí el archivo de prueba, elegí formato y subtítulos, y creá. Mostrá el proyecto en «Procesando».
3. Mientras procesa, abrí el proyecto **listo para revisar** que dejaste preparado.
4. **Revisión**: el video, la transcripción y los clips sugeridos. Elegí dos y tocá «Renderizar». Es el render de verdad.
5. **Clips**: aparecen en «Listo» cuando terminan. Descargá uno y abrilo.
6. **Brand kits**: abrí «Kit de la demo» y cambiá un color. Queda guardado en el backend.
7. **Publicar** un clip y mirar **Publicaciones**. Decilo: en esta demo la publicación es simulada.
8. **Comprar tokens**: en Uso → Comprar tokens elegí un paquete (por ejemplo 1.000 tokens) y tocá «Ir al pago». Se abre el checkout de prueba: tocá «Pagar». Vuelve a Uso con «Compra acreditada» y el saldo de la barra sube en esos tokens; en Movimientos aparece la compra. Decilo: el pago es simulado, nadie cobra. Con «Simular pago rechazado» se ve el aviso de pago rechazado y el saldo no cambia.
9. **Planes**: pasar a otro plan pago también pasa por el checkout de prueba y, al aprobar, se aplica al momento. Pasar a Gratis queda para el fin del período (con Pro de cortesía no hay período, así que se aplica al momento: mejor no mostrarlo, o correr `seed_demo` después para volver a Pro).
10. Volvé al proyecto del paso 2. Si ya está listo para revisar, mostralo.

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
| **Pagos**: Stripe no está integrado | Comprar tokens y cambiar de plan van al backend con el proveedor simulado: `/dev/checkout` hace de pasarela y no cobra nada. Si comprar dice «Los pagos todavía no están habilitados», `back/.env` tiene `PAYMENTS_PROVIDER` vacío: sacalo y `demo up -d --force-recreate web worker beat` |
| **Un ensayo cambió el plan** | `seed_demo` vuelve a poner Pro de cortesía, sin período ni bajada programada. Los tokens comprados en el ensayo quedan |
| **Un proveedor de IA falla o tarda** (key vencida, sin cuota, sin internet) | El proyecto queda en error y los tokens vuelven solos. Seguí con el proyecto preparado. Probá las keys el mismo día |
| **El video no tiene voz** | El proyecto falla con «no se detectó voz». Usá un video con alguien hablando |
| **La web dice «No pudimos conectar»** | `demo ps`: si `web` no está, `demo logs web --tail 50` y `demo up -d` |
| **Se abrió la web con otra dirección** | Los mocks no arrancan fuera de `localhost`. Volvé a `http://localhost:8080` |
| **Cambiaste `back/.env`** | `demo up -d --force-recreate web worker beat`. Lo que cambia en `docker-compose.demo.yml` para `front` pide `--build`. El proxy encuentra solo a los contenedores recreados, en unos segundos |
| **El puerto 8080 está ocupado** | `DEMO_PORT=9090 demo up -d --build`. La URL pública sale de `DEMO_PORT` y queda fija en el build de `front`: por eso el `--build`. Usá el mismo valor en cada comando `demo` (o ponelo en un `.env` en la raíz del repo) |

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
- [ ] **Decidir qué se dice** sobre lo simulado: pagos (el checkout de prueba no cobra), redes y publicaciones. Las invitaciones son reales, pero el email sale por consola (`demo logs worker`).
- [ ] **`back/docker/entrypoint.sh`** sigue sin dueño. No frena la demo, porque ni el `Dockerfile` ni los compose lo usan (las migraciones las corre `docker-compose.demo.yml`). Si alguien lo activa hoy, no arranca: espera `POSTGRES_HOST` y `POSTGRES_PORT`, que ningún compose define, y su última línea deja el contenedor vivo aunque Django se caiga.

## Sin Docker

Sirve para desarrollar, no para presentar. Con `DEBUG=True` y `USE_R2=False`, Django sirve los archivos de `back/media` en `/media/`, y la API devuelve esas URLs absolutas, con el origen del pedido (`http://localhost:8000/media/...`): el video de la revisión, la vista previa y las descargas andan aunque la web esté en otro puerto. Un límite: el servidor de desarrollo no responde pedidos por rango, así que adelantar o retroceder un video puede no funcionar. Con el proxy de la demo sí.

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
- Workspaces y miembros contra el backend, con la web construida con la lista de la demo (sin Docker ni proxy, la web y la API en puertos distintos): renombrar, crear, cambiar, transferir y borrar un workspace, y en Miembros ver la lista, cambiar un rol y quitar a alguien. Invitar y Reenviar siguen en mocks y no se reflejan en la lista.
- Django sirve `/media/` con `DEBUG=True` y `USE_R2=False`, y no lo hace con `DEBUG` apagado ni con R2 (tests en `apps/core/tests/test_media_serving.py`).
- El proxy sigue respondiendo después de recrear `web` y `front` con otra IP (probado con contenedores de reemplazo en una red de Docker), y `config` muestra solo dos puertos publicados, los dos en `127.0.0.1`.
- Con archivos locales, la API devuelve las URLs de `/media/` absolutas, con el origen del pedido. Detrás del proxy de la demo quedan en `http://localhost:8080/media/...` (tests en `apps/videos/tests/test_media_urls.py`; el recorrido en el navegador se hizo antes de este cambio, con la URL relativa).

Sin verificar:

- **La imagen del backend y el `up` completo en Docker.** La imagen no se construyó (pesa varios GB) y Docker Hub cortó la descarga de Postgres por límite de pedidos. Los servicios se probaron por separado, como dice arriba. **Hacé un `demo up -d --build` completo antes del lunes.**
- **El análisis con IA en vivo** (transcripción con voz, moderación y selección de clips): no había keys. Un video sin voz falla donde tiene que fallar y devuelve los tokens.
- **Confirmar una publicación simulada** hasta el final, y el paso por Uso y Planes más allá de abrir las pantallas.
- **Links de YouTube y Vimeo**, Ayrshare y Stripe.
