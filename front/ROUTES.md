# Rutas de OneCreator

> Archivo generado desde `src/dev/screens.ts` con `npm run contract:doc`. Para sumar una pantalla, agregala ahí y volvé a generarlo.

Cada pantalla de [`documents/producto/mapa-de-pantallas.md`](../documents/producto/mapa-de-pantallas.md) tiene su ruta. Las que todavía no están construidas muestran un cartel con su código; el dominio dueño reemplaza el `page.tsx` y pone su código en `src/features/<dominio>/`.

Los artboards están en el canvas de diseño, un archivo `<nombre>.dc.html` por pantalla. Los `M*` son la misma pantalla en el teléfono y los `R*` son variantes por rol.

| Código | Pantalla | Ruta | Archivo | Artboards | Dominio | Zona | Roles |
|---|---|---|---|---|---|---|---|
| A1 | Iniciar sesión | `/login` | `src/app/(public)/login/page.tsx` | `A1-Login`, `M12-Movil-login` | acceso | Pública | Todos |
| A2 | Crear cuenta | `/registro` | `src/app/(public)/registro/page.tsx` | `A2-Registro` | acceso | Pública | Todos |
| A3 | Verificar email | `/verificar` | `src/app/(public)/verificar/page.tsx` | `A3-Verificar-email` | acceso | Pública | Todos |
| A4 | Recuperar contraseña | `/recuperar` | `src/app/(public)/recuperar/page.tsx` | `A4-Recuperar` | acceso | Pública | Todos |
| A5 | Aceptar invitación | `/invitacion` | `src/app/(public)/invitacion/page.tsx` | `A5-Invitacion` | acceso | Pública | Todos |
| A6 | Precios | `/precios` | `src/app/(public)/precios/page.tsx` | `A6-Precios` | uso | Pública | Todos |
| B1 | Bienvenida | `/bienvenida` | `src/app/(onboarding)/bienvenida/page.tsx` | `B1b-Bienvenida-paso1`, `B1-Bienvenida`, `B1c-Bienvenida-paso3` | acceso | Primera vez (con sesión, sin barra) | Todos |
| B2 | Proyectos | `/proyectos` | `src/app/(app)/proyectos/page.tsx` | `B2-Proyectos`, `B2b-Proyectos-vacio`, `B2c-Proyectos-grilla`, `M1-Movil-proyectos` | proyectos | App (con sesión) | Todos |
| C1 | Nuevo proyecto | `/proyectos/nuevo` | `src/app/(app)/proyectos/nuevo/page.tsx` | `C1-Nuevo-proyecto`, `C1b-Nuevo-con-IA`, `C1c-Nuevo-errores`, `C1d-Nuevo-link`, `C1e-Nuevo-plan-gratis`, `M4-Movil-nuevo` | proyectos | App (con sesión) | Dueño, Admin, Editor |
| D1 | Proyecto · Resumen | `/proyectos/[id]` | `src/app/(app)/proyectos/[id]/(tabs)/page.tsx` | `D1-Proyecto-procesando`, `D1b-Proyecto-en-espera`, `D1c-Proyecto-error`, `D1d-Proyecto-listo`, `M2-Movil-proyecto` | proyecto | App (con sesión) | Todos |
| D1e | Proyecto · Transcripción | `/proyectos/[id]/transcripcion` | `src/app/(app)/proyectos/[id]/(tabs)/transcripcion/page.tsx` | `D1e-Proyecto-transcripcion` | proyecto | App (con sesión) | Todos |
| D1f | Proyecto · Actividad | `/proyectos/[id]/actividad` | `src/app/(app)/proyectos/[id]/(tabs)/actividad/page.tsx` | `D1f-Proyecto-actividad` | proyecto | App (con sesión) | Todos |
| D2 | Revisión de clips | `/proyectos/[id]/revision` | `src/app/(app)/proyectos/[id]/revision/page.tsx` | `D2-Revision`, `D2b-Revision-sin-sugerencias`, `M3-Movil-revision` | revision | App (con sesión) | Dueño, Admin, Editor (Lector: solo lectura) |
| D3 | Proyecto · Clips | `/proyectos/[id]/clips` | `src/app/(app)/proyectos/[id]/(tabs)/clips/page.tsx` | `D3-Clips`, `M6-Movil-clips`, `R2-Rol-lector-clips` | proyecto | App (con sesión) | Todos |
| D4 | Editor de clip | `/proyectos/[id]/clips/[clipId]` | `src/app/(app)/proyectos/[id]/clips/[clipId]/page.tsx` | `D4-Editor-clip`, `M7-Movil-editor` | revision | App (con sesión) | Dueño, Admin, Editor |
| D5 | Proyecto · Resultado (video completo) | `/proyectos/[id]/resultado` | `src/app/(app)/proyectos/[id]/(tabs)/resultado/page.tsx` | `D5-Resultado-video-completo` | proyecto | App (con sesión) | Todos |
| E2 | Publicaciones | `/publicaciones` | `src/app/(app)/publicaciones/page.tsx` | `E2-Publicaciones-calendario`, `E2b-Publicaciones-lista`, `E2c-Publicaciones-mes`, `E2d-Publicaciones-vacio`, `M8-Movil-publicaciones` | publicar | App (con sesión) | Todos |
| F1 | Brand kits | `/brand-kits` | `src/app/(app)/brand-kits/page.tsx` | `F1-Brand-kits`, `F1b-Brand-kits-vacio`, `M11-Movil-brand-kits` | brand-kits | App (con sesión) | Todos |
| F2 | Editar brand kit | `/brand-kits/[id]` | `src/app/(app)/brand-kits/[id]/page.tsx` | `F2-Brand-kit-editar` | brand-kits | App (con sesión) | Dueño, Admin, Editor |
| G1 | Uso · Resumen | `/uso` | `src/app/(app)/uso/page.tsx` | `G1-Uso`, `M9-Movil-uso`, `R1-Rol-editor-uso` | uso | App (con sesión) | Dueño y Admin (Editor y Lector ven solo el saldo: R1) |
| G2 | Uso · Movimientos | `/uso/movimientos` | `src/app/(app)/uso/movimientos/page.tsx` | `G2-Movimientos` | uso | App (con sesión) | Dueño y Admin |
| G3 | Uso · Planes | `/uso/planes` | `src/app/(app)/uso/planes/page.tsx` | `G3-Planes` | uso | App (con sesión) | Dueño y Admin cambian el plan |
| G4 | Uso · Comprar tokens | `/uso/comprar` | `src/app/(app)/uso/comprar/page.tsx` | `G4-Comprar-tokens` | uso | App (con sesión) | Dueño y Admin |
| G5 | Uso · Resultado de la compra | `/uso/comprar/resultado` | `src/app/(app)/uso/comprar/resultado/page.tsx` | `G5-Compra-resultado` | uso | App (con sesión) | Dueño y Admin |
| H1 | Ajustes · General | `/ajustes` | `src/app/(app)/ajustes/page.tsx` | `H1-Ajustes-general`, `M10-Movil-ajustes` | ajustes | App (con sesión) | Renombrar: Dueño y Admin. Zona de peligro: Dueño |
| H2 | Ajustes · Miembros | `/ajustes/miembros` | `src/app/(app)/ajustes/miembros/page.tsx` | `H2-Miembros` | ajustes | App (con sesión) | Dueño y Admin |
| H3 | Ajustes · Redes sociales | `/ajustes/redes` | `src/app/(app)/ajustes/redes/page.tsx` | `H3-Redes` | ajustes | App (con sesión) | Dueño y Admin |
| I1 | Perfil | `/perfil` | `src/app/(app)/perfil/page.tsx` | `I1-Perfil` | ajustes | App (con sesión) | Todos |
| I2 | Perfil · Notificaciones | `/perfil/notificaciones` | `src/app/(app)/perfil/notificaciones/page.tsx` | `I2-Notificaciones` | ajustes | App (con sesión) | Todos |
| K2 | Sin permiso | `/sin-permiso` | `src/app/(app)/sin-permiso/page.tsx` | `04-Sistema` | base | Sistema | Todos |
| K5 | Mantenimiento | `/mantenimiento` | `src/app/mantenimiento/page.tsx` | `04-Sistema` | base | Sistema | Todos |

## Piezas sin ruta propia

| Código | Pieza | Dónde vive | Artboards | Dominio |
|---|---|---|---|---|
| E1 | Publicar (panel lateral) | Panel sobre Clips, Resultado y Publicaciones. Convención: `?publicar=<clipId>[,<clipId>…]` en la URL de la pantalla que lo abre. | `E1-Publicar`, `E1b-Publicar-instagram`, `E1c-Publicar-varios`, `M5-Movil-publicar` | publicar |
| J1 | Menú de comandos (⌘K) | App shell: `src/shell/CommandMenu.tsx`. | `03-Transversales` | base |
| J2 | Notificaciones (campana) | App shell: `src/shell/NotificationsPanel.tsx`. | `03-Transversales` | base |
| J3 | Selector de workspace | App shell: `src/shell/WorkspaceSwitcher.tsx`. | `03-Transversales` | base |
| J4 | Toasts | `useToast()` de `@/ui`. | `03-Transversales`, `01-Sistema-visual` | base |
| K1 | No encontrado (404) | `src/app/not-found.tsx` y `<NotFoundMessage>`. | `04-Sistema` | base |
| K3 | Error del servidor (500) | `src/app/error.tsx`, `src/app/global-error.tsx` y `<ServerErrorMessage>`. | `04-Sistema` | base |
| K4 | Sin conexión | Banner fijo: `src/shell/OfflineBanner.tsx`. | `04-Sistema` | base |
| D1 (diálogo) | Confirmar costo | `<ConfirmCostDialog>` de `@/dialogs`. | `02-Dialogos` | base |
| D2 (diálogo) | Saldo insuficiente | `useBillingDialogs()` de `@/dialogs`. | `02-Dialogos` | base |
| D3 (diálogo) | Límite del plan | `useBillingDialogs()` de `@/dialogs`. | `02-Dialogos` | base |
| D4 (diálogo) | Borrar proyecto | `<DeleteProjectDialog>` de `@/dialogs`. | `02-Dialogos` | base |
| D5 (diálogo) | Contenido bloqueado | `<ContentBlockedDialog>` de `@/dialogs`. | `02-Dialogos` | base |
| D6 (diálogo) | Conectar red social | `<ConnectNetworkDialog>` de `@/dialogs`. | `02-Dialogos` | base |
| D7 (diálogo) | Invitar miembro | A construir en `src/features/ajustes/` con `<Dialog>`; hooks `useMembers` y `useInviteMembers`. | `02-Dialogos` | ajustes |
| D8 (diálogo) | Cambios sin guardar | `<UnsavedChangesDialog>` de `@/dialogs` y `useBeforeUnload()`. | `02-Dialogos` | base |
| D9 (diálogo) | Sesión vencida | `<SessionExpiredDialog>`, montado en el layout raíz. | `02-Dialogos` | base |

## Rutas de desarrollo

| Ruta | Para qué |
|---|---|
| `/dev/kit` | Todos los componentes del sistema visual en los dos temas, y el selector de escenarios de datos de prueba. |
| `/dev/checkout` | Solo con mocks (modos mock e hybrid): reemplaza al checkout del proveedor de pagos (aprueba o rechaza la compra y vuelve). Con `billing` en el backend decide con `billing.simulatePurchase`, el proveedor simulado (`PAYMENTS_PROVIDER=simulated`). |
| `/dev/conectar-red` | Solo con mocks: reemplaza a la ventana de autorización de la red social. |

## Dominios

| Dominio | Pantallas | Carpeta |
|---|---|---|
| acceso | A1, A2, A3, A4, A5, B1 | `src/features/acceso/` |
| uso | A6, G1, G2, G3, G4, G5 | `src/features/uso/` |
| proyectos | B2, C1 | `src/features/proyectos/` |
| proyecto | D1, D1e, D1f, D3, D5 | `src/features/proyecto/` |
| revision | D2, D4 | `src/features/revision/` |
| publicar | E2, E1 | `src/features/publicar/` |
| brand-kits | F1, F2 | `src/features/brand-kits/` |
| ajustes | H1, H2, H3, I1, I2, D7 (diálogo) | `src/features/ajustes/` |
| base | K2, K5, J1, J2, J3, J4, K1, K3, K4, D1 (diálogo), D2 (diálogo), D3 (diálogo), D4 (diálogo), D5 (diálogo), D6 (diálogo), D8 (diálogo), D9 (diálogo) | `src/ui`, `src/shell`, `src/dialogs`, `src/api`, `src/lib` |

## Protección de rutas

- Todo lo de `(app)` y `(onboarding)` pasa por `<RequireSession>`: sin sesión va a `/login?siguiente=<ruta>`.
- Una cuenta nueva que entra a `/proyectos` va a `/bienvenida` (`needsWelcome()` en `src/shell/session.tsx`); al terminar o saltear, la pantalla llama a `markWelcomeSeen()`.
- Lo que el rol no permite se muestra deshabilitado con «Pedile acceso a un admin»: `usePermissions().gate(acción)` devuelve las props para el botón. Solo las secciones de admin de Ajustes se ocultan (`<RequireRole>` muestra K2 si se entra por URL).
