# ⚖️ Guía de Contribución — OneCreator

> **Lectura obligatoria antes de contribuir.**
> Al enviar código a este repositorio, aceptás los términos del **Acuerdo de Contribución (CLA)** que se detalla al final de este documento. Si no estás de acuerdo, no abras Pull Requests.

---

## Tabla de Contenidos

- [Contexto Académico](#contexto-académico)
- [Primeros Pasos](#primeros-pasos)
- [Workflow de Desarrollo](#workflow-de-desarrollo)
- [Convenciones de Código](#convenciones-de-código)
- [Proceso de Pull Request](#proceso-de-pull-request)
- [Acuerdo de Contribución (CLA)](#-acuerdo-de-contribución-cla)

---

## Contexto Académico

Este repositorio es un entorno de desarrollo creado como trabajo práctico para la **Universidad Tecnológica Nacional (UTN)**. La participación como contribuyente es voluntaria y se enmarca exclusivamente dentro de los objetivos de la cátedra correspondiente.

La arquitectura base, el modelo de negocio y la propiedad intelectual del proyecto son de **Romeo Lorenzo Monfroglio** (ver [LICENSE](./LICENSE)).

---

## Primeros Pasos

### 1. Clonar y configurar

```bash
git clone https://github.com/RomeKai/videoeditUTN.git
cd videoeditUTN
```

### 2. Crear tu branch de trabajo

```bash
# Siempre partir de dev — nunca trabajar directo sobre main o dev
git checkout dev
git pull origin dev
git checkout -b feature/<tu-nombre>/<descripcion-breve>
```

**Convención de nombres de branch:**

| Tipo | Formato | Ejemplo |
|------|---------|---------|
| Feature nueva | `feature/<nombre>/<desc>` | `feature/juan/social-auth-google` |
| Bug fix | `fix/<nombre>/<desc>` | `fix/maria/wallet-race-condition` |
| Refactor | `refactor/<nombre>/<desc>` | `refactor/pedro/cleanup-imports` |

### 3. Entorno de desarrollo

```bash
# Levantar toda la infraestructura con un solo comando
docker compose up -d

# Verificar que los 4 servicios están corriendo
docker compose ps
# Esperado: db, redis, web (port 8001), worker — todos "Up"

# Correr migraciones (solo la primera vez)
docker compose exec web python manage.py migrate

# Crear superusuario de prueba (solo la primera vez)
docker compose exec web python manage.py createsuperuser
```

### 4. Configurar variables de entorno

Copiá `back/.env.example` a `back/.env` y completá las variables requeridas:

```bash
cp back/.env.example back/.env
```

> **⚠️ Nunca commitear `back/.env`.** Contiene secretos. El `.gitignore` ya lo protege.

---

## Workflow de Desarrollo

### Ciclo de trabajo

```
dev (rama principal de desarrollo)
 └── feature/<tu-nombre>/<desc>   ← tu branch de trabajo
      └── Push → Pull Request → Code Review → Merge a dev
```

1. Trabajá **siempre** en tu branch personal.
2. Hacé commits frecuentes con mensajes descriptivos.
3. Cuando el feature esté completo, abrí un **Pull Request hacia `dev`**.
4. Esperá la revisión y aprobación antes del merge.

### Comandos útiles durante el desarrollo

```bash
# Ver logs del worker (tareas Celery)
docker compose logs worker --tail 50

# Ver logs del web (API Django)
docker compose logs web --tail 50

# Ejecutar un comando Django dentro del container
docker compose exec web python manage.py shell

# Correr los tests
docker compose exec web python manage.py test
```

---

## Convenciones de Código

### Commits: Conventional Commits

Todos los mensajes de commit deben seguir el formato [Conventional Commits](https://www.conventionalcommits.org/):

```
<tipo>(<alcance>): <descripción corta>

[cuerpo opcional]
```

**Tipos permitidos:**

| Tipo | Uso |
|------|-----|
| `feat` | Nueva funcionalidad |
| `fix` | Corrección de bug |
| `refactor` | Cambio de código que no agrega feature ni corrige bug |
| `docs` | Cambios en documentación |
| `test` | Agregar o modificar tests |
| `chore` | Mantenimiento, configuración, dependencias |

**Ejemplos:**
```
feat(videos): add face tracking toggle to project creation
fix(payments): prevent race condition in wallet reserve
docs(readme): update Docker setup instructions
```

### Python

- **Estilo:** PEP 8.
- **Imports:** Agrupados por stdlib → third-party → local, separados por líneas vacías.
- **Idioma del código:** Inglés para nombres de variables, funciones, clases y comentarios técnicos. El contenido de UI puede estar en español.
- **Docstrings:** Obligatorios en clases y funciones públicas.

### Django

- **Views:** Usar ViewSets con `@action` decorators para endpoints custom.
- **Modelos:** `TextChoices` para enums. UUIDs como primary keys.
- **Settings:** Toda variable sensible va en `.env` y se lee con `django-environ`.
- **Migraciones:** Siempre commitear las migraciones. Nunca ejecutar `--fake`.

---

## Proceso de Pull Request

### Antes de abrir el PR

- [ ] El código compila sin errores: `docker compose exec web python manage.py check`
- [ ] Las migraciones están al día: `docker compose exec web python manage.py makemigrations --check`
- [ ] Los tests pasan (si aplica): `docker compose exec web python manage.py test`
- [ ] No hay secretos, API keys o rutas absolutas hardcodeadas
- [ ] Los commits siguen Conventional Commits

### Template del PR

```markdown
## Descripción
Breve explicación de qué hace este cambio y por qué.

## Tipo de cambio
- [ ] Feature nueva
- [ ] Bug fix
- [ ] Refactor
- [ ] Documentación

## Archivos modificados
- `archivo1.py` — qué se cambió
- `archivo2.py` — qué se cambió

## Testing
Describí cómo probaste los cambios.

## Screenshots (si aplica)
```

### Revisión

- Todo PR necesita **al menos 1 aprobación** antes del merge.
- El autor del proyecto ([@RomeKai](https://github.com/RomeKai)) tiene la decisión final.
- Los PRs se mergean con **Squash and Merge** para mantener un historial limpio.

---

## ⚖️ Acuerdo de Contribución (CLA)

Para participar en el desarrollo de este repositorio académico, todos los contribuyentes aceptan las siguientes condiciones:

### 1. Reconocimiento Académico
El código aportado será utilizado para la evaluación y calificación de la cátedra correspondiente en la UTN. Todos los contribuyentes serán mencionados en la sección de créditos del proyecto con su nombre y rol.

### 2. Cesión de Derechos Patrimoniales
Al abrir un Pull Request (PR) hacia cualquier rama de este repositorio, el contribuyente **cede voluntaria y gratuitamente** los derechos de explotación, uso comercial y modificación del código aportado a favor del autor original del proyecto (**Romeo Lorenzo Monfroglio**). El contribuyente conserva el derecho moral de ser reconocido como co-autor de las porciones de código que haya escrito.

### 3. Ausencia de Relación Comercial
La participación en este repositorio **no constituye** una relación laboral, comercial, societaria ni de ningún otro tipo entre los contribuyentes y el autor original. No se generan expectativas de compensación económica, participación en ganancias ni derechos patrimoniales futuros sobre el producto derivado de este código.

### 4. Uso del Código Aportado
El autor original se reserva el derecho exclusivo de:
- Utilizar el código aportado con fines comerciales.
- Modificar, adaptar, integrar o descartar cualquier contribución.
- Sublicenciar el código resultante bajo los términos que considere convenientes.

### 5. Alcance de la Contribución
Las contribuciones se aceptan "tal cual" (*as-is*). El autor original no está obligado a mantener, integrar ni dar soporte a ninguna contribución recibida.

### 6. Aceptación Implícita
**Al abrir un Pull Request o enviar código de cualquier forma a este repositorio, el contribuyente declara haber leído, comprendido y aceptado todos los términos de este acuerdo.**

---

> 📩 **Preguntas o consultas:** Contactar a Romeo Lorenzo Monfroglio a través de los canales de la cátedra o vía GitHub.
