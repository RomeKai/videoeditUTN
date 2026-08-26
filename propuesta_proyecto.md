# Propuesta de Proyecto — Ingeniería de Software en la Nube

---

**Materia:** Ingeniería de Software en la Nube
**Institución:** Universidad Tecnológica Nacional — Facultad Regional La Plata
**Ciclo Lectivo:** 2026

---

## Integrantes del Equipo

| Nombre completo | Legajo |
|----------------|--------|
| Franco Jimenez | 31848 |
| Agustin Gonzalez Blasco | 31303 |
| Franco Javier Portillo Colinas | 31089 |
| Bautista Calvo | 32156 |
| Pedro Moyano Amaya | 31411 |

---

## 1. Nombre del Proyecto

### **OneCreator — All-in-One Viral Studio**

Plataforma SaaS de edición automatizada de video impulsada por Inteligencia Artificial, diseñada para transformar contenido de formato largo en clips virales optimizados para redes sociales.

---

## 2. Descripción del Problema

La economía del creador de contenido atraviesa una tensión estructural entre la demanda de volumen y la capacidad operativa de producción. Las plataformas de consumo rápido —TikTok, Instagram Reels, YouTube Shorts— premian la publicación frecuente de clips cortos, verticales y altamente editados. Sin embargo, el proceso para obtener estos clips a partir de material fuente de formato largo sigue siendo predominantemente manual, costoso y prohibitivamente lento.

Un creador de contenido promedio invierte entre **4 y 8 horas** en editar un video de 30 minutos para extraer 3 a 5 clips virales. Este proceso incluye: la revisión completa del material, la identificación subjetiva de momentos de alto impacto, el recorte temporal, la adaptación de relación de aspecto (de 16:9 horizontal a 9:16 vertical), la adición de subtítulos, el ajuste de encuadre para mantener al sujeto centrado, y finalmente la exportación en los formatos específicos de cada plataforma.

Las herramientas de edición tradicionales (Adobe Premiere Pro, DaVinci Resolve, Final Cut Pro) son potentes pero exigen una curva de aprendizaje pronunciada y un flujo de trabajo completamente manual. Esto genera tres fricciones críticas:

1. **Barrera de entrada técnica:** Los creadores emergentes carecen de las habilidades técnicas o el presupuesto para contratar editores profesionales, limitando su capacidad de crecimiento.
2. **Time-to-Market prohibitivo:** En un ecosistema donde la viralidad tiene una ventana de horas, un ciclo de edición de días es una desventaja competitiva insuperable.
3. **Escalabilidad nula:** El proceso manual no escala. Un creador que produce 4 videos semanales necesita 16 a 32 horas de edición, convirtiendo la creación de contenido en un trabajo de tiempo completo solo en post-producción.

El mercado exige una solución que automatice las tareas repetitivas de edición, que aplique inteligencia artificial para detectar los momentos de mayor potencial viral, y que reduzca el ciclo de producción de horas a minutos, sin sacrificar la calidad perceptual del producto final.

---

## 3. Propuesta de Valor

**OneCreator** aborda las fricciones descritas mediante una plataforma cloud-native que integra Inteligencia Artificial como diferenciador principal de negocio en cada etapa del flujo de producción de contenido. La solución minimiza el Time-to-Market del creador al automatizar las tareas de mayor costo temporal, manteniendo al humano como director creativo del proceso.

### Funcionalidades Clave del MVP

**Recorte Inteligente de Mejores Momentos:**
El sistema ingiere un video de formato largo, ejecuta transcripción automática con word-level timestamps mediante OpenAI Whisper (procesado localmente en CPU/GPU), y luego envía la transcripción a un modelo LLM (GPT-4o / GPT-4o-mini) que identifica y selecciona los segmentos con mayor potencial viral. Cada clip propuesto incluye una justificación explícita del modelo (IA explicable), un score de viralidad y los timestamps precisos de corte.

**Face Tracking y Dynamic Cropping:**
Un motor de detección facial basado en MediaPipe genera trayectorias de seguimiento suavizadas para mantener al sujeto centrado durante la conversión de 16:9 a 9:16. El sistema soporta múltiples layouts de renderizado (relleno, fondo borroso, split screen gaming, picture-in-picture, versus, speaker dinámico), permitiendo al creador elegir el estilo visual que mejor se adapte a su contenido.

**Subtítulos Dinámicos con IA:**
Generación automática de subtítulos word-level animados a partir de la transcripción, con configuración de estilo (color, tamaño, posición, fuente) y soporte para traducción a múltiples idiomas mediante LLM.

**Prompt-to-Edit (Edición por Lenguaje Natural):**
Sistema donde el usuario describe en lenguaje natural el tipo de edición deseada (ej: *"Cortá los momentos de humor, subtítulos grandes amarillos, layout vertical con fondo borroso"*) y la IA traduce ese prompt a una configuración completa de proyecto que alimenta el pipeline de procesamiento existente, sin duplicar lógica.

**Distribución Multi-Plataforma:**
Motor de publicación directa a TikTok, Instagram Reels y YouTube Shorts mediante OAuth, con generación automática de captions, hashtags optimizados y hora de publicación recomendada por plataforma. Incluye programación de contenido con calendario y adaptación de formato a los requisitos técnicos de cada red social.

**Modelo de Monetización Flexible:**
Sistema de wallet transaccional con tokens para edición de video (modelo pay-per-use) complementado con planes de suscripción mensual que habilitan funcionalidades premium (resolución 4K, SEO optimization, face tracking avanzado). Los usuarios del plan gratuito reciben sus videos con marca de agua del producto.

**Transparencia y Control Humano:**
Cada decisión de la IA se documenta con un rationale log explicable. El usuario revisa, aprueba o ajusta las propuestas antes del renderizado. El sistema no toma decisiones irreversibles sin aprobación explícita del creador.

---

## 4. Usuarios Objetivo

La plataforma está diseñada para tres segmentos primarios de mercado, cada uno con necesidades y disposición de pago diferenciadas:

### Creadores de Contenido Independientes
Youtubers, streamers, podcasters y creadores de TikTok que producen contenido de formato largo y necesitan adaptarlo a clips cortos para múltiples plataformas. Representan el segmento de mayor volumen y menor ticket promedio. Su principal dolor es el tiempo de edición y la falta de habilidades técnicas. Valorarán especialmente el Prompt-to-Edit, los subtítulos automáticos y la publicación multi-plataforma con un solo clic.

### Agencias de Marketing Digital
Empresas que gestionan la presencia en redes sociales de múltiples clientes simultáneamente. Requieren volumen de producción alto, consistencia de marca (BrandKits con colores, fuentes y logos corporativos), y flujos de aprobación antes de publicar. Representan el segmento de mayor lifetime value y disposición a pagar por features premium como resolución 4K, eliminación de marca de agua y scheduling avanzado.

### Equipos Corporativos de Comunicación
Departamentos de comunicación interna y marketing de empresas medianas y grandes que necesitan producir contenido de video para capacitación, difusión institucional y presencia en redes. Valorarán la colaboración en equipo (workspaces con roles de admin, editor y viewer), la seguridad del almacenamiento en la nube, y la trazabilidad de las decisiones de IA para cumplimiento normativo.

---

## 5. Stack Tecnológico — Arquitectura Cloud-Native

El stack tecnológico de OneCreator fue seleccionado en función de los requisitos específicos del procesamiento de video pesado, la orquestación de tareas de IA de larga duración y la escalabilidad horizontal en entornos cloud. A continuación se documenta cada componente extraído directamente del código fuente y la configuración del proyecto.

### 5.1 Diagrama de Arquitectura

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│   Cliente    │────▶│   API Web   │────▶│   PostgreSQL 15  │
│  (Frontend)  │     │  (Django 5) │     │  (Persistencia)  │
└─────────────┘     └──────┬──────┘     └──────────────────┘
                           │
                    Celery Task
                           │
                    ┌──────▼──────┐     ┌──────────────────┐
                    │   Worker    │────▶│    Redis 7       │
                    │    (IA)     │     │  (Broker/Cache)  │
                    └──────┬──────┘     └──────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Whisper  │ │  OpenAI  │ │  FFmpeg  │
        │(Transc.) │ │ GPT-4o   │ │  + Mpy2  │
        └──────────┘ └──────────┘ └──────────┘
                                        │
                                        ▼
                                ┌──────────────┐
                                │Cloudflare R2 │
                                │ (S3-compat.) │
                                └──────────────┘
```

### 5.2 Componentes del Stack

#### Frontend (por definir — Mes 3 del roadmap)
| Componente | Tecnología | Justificación |
|-----------|-----------|---------------|
| Framework UI | React / Next.js 14+ (App Router) | SSR para SEO, API routes para BFF pattern |
| Comunicación API | Fetch + JWT Bearer tokens | Autenticación stateless compatible con SimpleJWT |

> **Nota:** El frontend se encuentra en fase de definición. El esfuerzo del equipo se concentra primero en la robustez del core backend, y el frontend será implementado en el último mes del cronograma.

#### Backend / API
| Componente | Tecnología | Versión | Justificación |
|-----------|-----------|---------|---------------|
| Framework web | Django | 5.x | ORM maduro, admin integrado, ecosistema de seguridad robusto |
| API REST | Django REST Framework (DRF) | 3.x | Serializers, ViewSets, permisos granulares por recurso |
| Autenticación | SimpleJWT | — | Tokens JWT stateless con access (25 min) + refresh (3 días) + rotación |
| Documentación API | drf-spectacular | — | Generación automática de esquema OpenAPI 3.0 (Swagger UI + ReDoc) |
| CORS | django-cors-headers | — | Control de orígenes cruzados para comunicación con frontend SPA |
| Variables de entorno | django-environ | — | Lectura de `.env` con tipado y defaults, separación config/código |
| Servidor WSGI (prod) | Gunicorn | — | Servidor pre-fork eficiente para producción |
| Archivos estáticos | WhiteNoise | — | Servir statics desde el mismo proceso sin Nginx en dev |

#### Persistencia de Datos
| Componente | Tecnología | Versión | Justificación |
|-----------|-----------|---------|---------------|
| Base de datos relacional | PostgreSQL | 15 (Alpine) | Soporte JSONB para datos semi-estructurados (transcripts, AI rationale), integridad referencial, índices compuestos |
| Driver | psycopg2-binary | — | Conector C optimizado para PostgreSQL |
| Cache / Locks | django-redis | — | Cache distribuido y locks distribuidos para operaciones concurrentes (wallet) |

#### Storage / CDN
| Componente | Tecnología | Justificación |
|-----------|-----------|---------------|
| Almacenamiento de objetos | Cloudflare R2 | Compatible con S3 API (boto3), **zero egress fees** — crítico para un SaaS de video donde el tráfico de descarga de archivos pesados domina los costos operativos |
| SDK de acceso | boto3 | Cliente S3-compatible para upload/download programático |
| Modo desarrollo | Bypass local (`USE_S3=False`) | Permite desarrollo sin credenciales de cloud, usando filesystem local |

#### Inteligencia Artificial
| Componente | Tecnología | Modo | Justificación |
|-----------|-----------|------|---------------|
| Transcripción | OpenAI Whisper | **Local** (CPU/GPU) | Word-level timestamps, sin dependencia de API externa para transcripción, sin costo por llamada |
| Selección de clips | OpenAI GPT-4o / GPT-4o-mini | API remota | Análisis de transcripción para detección de momentos virales, con JSON Structured Outputs |
| SEO y metadata | OpenAI GPT-4o-mini | API remota | Generación de captions, hashtags y recomendaciones de publicación por plataforma |
| Detección facial | MediaPipe (TFLite) | **Local** | Face detection para dynamic cropping, sin latencia de red, sin costo variable |
| Seguridad IA | AI Security Shield (custom) | Local | Aislamiento de input de usuario en prompts LLM para prevenir prompt injection |
| Framework numérico | NumPy + OpenCV (headless) | Local | Procesamiento de frames para tracking y análisis visual |
| Deep Learning runtime | PyTorch (CPU) | Local | Backend de ejecución para Whisper y modelos de audio |

#### Procesamiento de Video
| Componente | Tecnología | Justificación |
|-----------|-----------|---------------|
| Encoding / decoding | FFmpeg (sistema) | Estándar industrial para conversión de formatos, generación de proxy, y removal de silencios |
| Composición y render | MoviePy 2.0 | API Python para composición de video (layout, subtítulos, watermark), basada en FFmpeg |
| Subtítulos | SubtitleEngine (custom) | Motor propietario de subtítulos word-level animados con estilos configurables |
| Validación de archivos | python-magic | Verificación de MIME type real del archivo subido (seguridad anti-spoofing) |

#### Orquestación de Tareas Asíncronas
| Componente | Tecnología | Versión | Justificación |
|-----------|-----------|---------|---------------|
| Task queue | Celery | 5.x | Orquestación de tareas de larga duración (transcripción, render) con retry, chains y groups |
| Message broker | Redis | 7 (Alpine) | Broker ligero y rápido para Celery, también utilizado como result backend y cache |
| Scheduling | Celery Beat | — | Ejecución periódica de tareas programadas (dispatch de publicaciones en redes sociales) |

#### Contenedorización y Orquestación
| Componente | Tecnología | Justificación |
|-----------|-----------|---------------|
| Contenedores | Docker | Entorno reproducible con multi-stage builds (web liviano vs worker pesado con IA) |
| Orquestación local | Docker Compose | 4 servicios: `db`, `redis`, `web`, `worker` — un comando levanta todo el stack |
| Imagen base | python:3.11-slim | Imagen mínima con dependencias de sistema (FFmpeg, libgl1, ImageMagick) |

#### Testing
| Componente | Tecnología | Justificación |
|-----------|-----------|---------------|
| Framework | pytest + pytest-django | Fixtures, parametrize, plugins para Django ORM |
| Mocking | pytest-mock + factory-boy + Faker | Simulación de servicios externos (OpenAI), generación de datos de prueba |
| Cobertura | pytest-cov + coverage | Métricas de cobertura de código |
| Paralelismo | pytest-xdist | Ejecución paralela de tests para CI rápido |

### 5.3 Justificación del Stack

La selección tecnológica de OneCreator responde a tres principios fundamentales alineados con los objetivos de la cátedra de Ingeniería de Software en la Nube:

**Libertad tecnológica y ausencia de vendor lock-in.** Cada componente fue elegido por ser de código abierto o basado en estándares abiertos. Django, Celery, Redis, PostgreSQL y FFmpeg son proyectos open-source con comunidades maduras y documentación extensa. El almacenamiento en Cloudflare R2 utiliza la API de S3 como interfaz, lo que permite migrar a cualquier proveedor S3-compatible (AWS S3, MinIO, DigitalOcean Spaces) cambiando únicamente variables de entorno, sin modificar código de aplicación. Los modelos de IA se ejecutan localmente (Whisper, MediaPipe) o a través de APIs estandarizadas (OpenAI-compatible), evitando dependencia de un único proveedor de infraestructura.

**Escalabilidad horizontal cloud-native.** La arquitectura separa explícitamente el plano de datos (API web, liviana) del plano de cómputo (worker IA, pesado). Esta separación permite escalar cada componente de forma independiente: en períodos de alta demanda se agregan workers de Celery sin afectar la API, y viceversa. El Dockerfile utiliza multi-stage builds para producir imágenes diferenciadas (`web` vs `worker`), optimizando el tamaño y el surface area de seguridad de cada contenedor. PostgreSQL y Redis operan como servicios internos sin exposición de puertos al host, siguiendo el principio de mínimo privilegio.

**Eficiencia en procesamiento de video pesado.** El procesamiento de video es computacionalmente intensivo y genera archivos de gran tamaño. La elección de Whisper en modo local (vs API) elimina la latencia de red y el costo por minuto de transcripción, factores críticos cuando se procesan videos de 30+ minutos. FFmpeg como motor de encoding ofrece rendimiento nativo en C con soporte de hardware acceleration. Cloudflare R2 como storage elimina los costos de egress (transferencia de salida) que en AWS S3 representarían el componente más caro de la operación de un SaaS de video. La arquitectura de task queue con Celery permite precargar modelos de IA una sola vez por proceso de worker y reutilizarlos entre tareas, amortizando el costo de inicialización de modelos que pesan 139+ MB.

---

*Documento generado para la cátedra de Ingeniería de Software en la Nube — UTN FRLP, 2026.*
