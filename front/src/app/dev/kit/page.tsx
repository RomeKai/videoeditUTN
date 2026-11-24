'use client';

import Link from 'next/link';
import { useState, type ReactNode } from 'react';
import { ScenarioPicker } from '@/dev/ScenarioPicker';
import {
  ConfirmCostDialog,
  ConfirmDialog,
  ConnectNetworkDialog,
  ContentBlockedDialog,
  UnsavedChangesDialog,
  useBillingDialogs,
} from '@/dialogs';
import { notifySessionExpired } from '@/api/session';
import { API_MOCKS } from '@/api/config';
import { POST_STATUS, TRANSACTION_KIND } from '@/lib/project-status';
import { Brand } from '@/shell/Brand';
import { NoAccessMessage, NotFoundMessage, ServerErrorMessage } from '@/shell/SystemPage';
import {
  ActionBar,
  Avatar,
  Badge,
  Banner,
  Button,
  Card,
  CardFoot,
  CardHead,
  Checkbox,
  ClipCard,
  CostBreakdown,
  Crumbs,
  DateTime,
  Dialog,
  Drawer,
  DropdownMenu,
  Duration,
  EmptyState,
  Field,
  FileDropZone,
  FilterChips,
  FixedBanner,
  Icon,
  IconButton,
  Input,
  Kbd,
  KeyValue,
  Kpi,
  ListRow,
  ListRowMain,
  NavTabs,
  NetworkChip,
  PageHead,
  Pagination,
  PasswordInput,
  PlanLock,
  Popover,
  ProgressBar,
  ProjectHeader,
  Radio,
  RadioCard,
  RadioGroup,
  Score,
  SegmentedControl,
  Select,
  SettingsGroup,
  SettingsRow,
  Skeleton,
  SkeletonRows,
  SkeletonText,
  Slider,
  Spinner,
  StageStepper,
  StatusDot,
  StepSegments,
  Stepper,
  Switch,
  TabList,
  TabPanel,
  Table,
  Textarea,
  ThemePreferenceControl,
  ThemeToggle,
  Timecode,
  Tokens,
  Tooltip,
  useToast,
  VideoPlayer,
  VideoThumb,
  type IconName,
} from '@/ui';
import { ICON_PATHS } from '@/ui/icon-paths';
import styles from './kit.module.css';

const NOW = new Date('2026-11-24T14:30:00-03:00');

function Section({ id, n, title, note, children }: { id: string; n: string; title: string; note?: string; children: ReactNode }) {
  return (
    <section className={styles.section} id={id} aria-labelledby={`${id}-title`}>
      <div className={styles.head}>
        <span className="num subtle t-label">{n}</span>
        <h2 className="t-h2" id={`${id}-title`}>
          {title}
        </h2>
        {note ? <p className="t-small muted">{note}</p> : null}
      </div>
      <div className={styles.body}>{children}</div>
    </section>
  );
}

/** Renders the same specimen in both themes, side by side (or stacked for wide ones). */
function Duo({ stack = false, children }: { stack?: boolean; children: ReactNode }) {
  return (
    <div className={`${styles.duo} ${stack ? styles.stack : ''}`}>
      {(['dark', 'light'] as const).map((theme) => (
        <div key={theme} className={styles.theme} data-theme={theme}>
          <span className={styles.tag}>{theme === 'dark' ? 'Oscuro' : 'Claro'}</span>
          {children}
        </div>
      ))}
    </div>
  );
}

const TOKENS = ['--bg', '--bg-subtle', '--surface', '--surface-2', '--surface-3', '--border', '--border-strong', '--fg', '--fg-muted', '--fg-subtle', '--primary', '--accent', '--accent-fill', '--ok', '--warn', '--err', '--info', '--err-fill'];

const SECTIONS: Array<[string, string]> = [
  ['escenarios', 'Escenarios'],
  ['color', 'Color'],
  ['tipografia', 'Tipografía y números'],
  ['botones', 'Botones'],
  ['campos', 'Campos y controles'],
  ['estado', 'Estado y progreso'],
  ['avisos', 'Avisos y bloqueos'],
  ['identidad', 'Avatares, redes y miniaturas'],
  ['estructura', 'Estructura'],
  ['datos', 'Tablas y datos'],
  ['clips', 'Clip y proceso'],
  ['flotantes', 'Menús, diálogos y toasts'],
  ['video', 'Reproductor'],
  ['sistema', 'Sistema'],
  ['iconos', 'Íconos'],
];

function Buttons() {
  return (
    <>
      <div className={styles.row}>
        <Button variant="primary">Crear proyecto</Button>
        <Button variant="accent">Revisar clips</Button>
        <Button variant="secondary">Descargar</Button>
        <Button variant="ghost">Cancelar</Button>
        <Button variant="danger">Borrar proyecto</Button>
      </div>
      <div className={styles.row}>
        <Button variant="primary" size="sm" iconLeft="plus">
          Nuevo proyecto
        </Button>
        <Button variant="secondary" size="sm" iconLeft="download">
          Descargar
        </Button>
        <Button variant="primary">Mediano · 36</Button>
        <Button variant="accent" size="lg">
          Renderizar 6 clips
        </Button>
        <IconButton icon="more" label="Más opciones" />
        <IconButton icon="edit" label="Renombrar" tooltip />
      </div>
      <div className={styles.row}>
        <Button variant="primary" loading>
          Creando…
        </Button>
        <Button variant="secondary" loading>
          Guardando…
        </Button>
        <Button variant="primary" disabled>
          Primario deshabilitado
        </Button>
        <Button variant="accent" disabled>
          Acento deshabilitado
        </Button>
        <Button variant="secondary" disabled>
          Secundario
        </Button>
        <Button variant="primary" disabledReason="Pedile acceso a un admin">
          Bloqueado por rol
        </Button>
      </div>
    </>
  );
}

function Controls() {
  const [format, setFormat] = useState<'9:16' | '1:1' | '16:9'>('9:16');
  const [resolution, setResolution] = useState<'720p' | '1080p' | '4K'>('720p');
  const [faces, setFaces] = useState(true);
  const [bypass, setBypass] = useState(false);
  const [checked, setChecked] = useState(true);
  const [level, setLevel] = useState('fast');
  const [style, setStyle] = useState('dynamic');
  const [clips, setClips] = useState(8);
  const [zoom, setZoom] = useState(1.5);
  const [file, setFile] = useState<string | null>(null);
  return (
    <div className={styles.grid2}>
      <Card padded className={styles.col}>
        <Field label="Título del proyecto">
          <Input defaultValue="Vlog Mendoza" />
        </Field>
        <Field label="Link del video" hint="YouTube, Vimeo y otros sitios públicos.">
          <Input placeholder="https://youtube.com/watch?v=…" />
        </Field>
        <Field label="Email" error="Revisá el email: falta el dominio.">
          <Input defaultValue="lucia@estudionorte" />
        </Field>
        <Field label="Contraseña">
          <PasswordInput defaultValue="contraseña" autoComplete="off" />
        </Field>
        <Field label="Buscar" optional>
          <Input icon="search" placeholder="Buscar proyectos…" />
        </Field>
        <Field label="Rol" hint="El select trae su propia flecha.">
          <Select
            defaultValue="editor"
            options={[
              { value: 'editor', label: 'Editor' },
              { value: 'admin', label: 'Admin' },
              { value: 'viewer', label: 'Lector' },
            ]}
          />
        </Field>
        <Field label="¿Qué buscás?" hint={<span className="num">52 / 500</span>}>
          <Textarea defaultValue="Los momentos donde se habla de precios y objeciones." />
        </Field>
        <Field label="Nombre del workspace" hint="Deshabilitado.">
          <Input defaultValue="Estudio Norte" disabled />
        </Field>
      </Card>
      <Card padded className={styles.col}>
        <span className={styles.label}>Control segmentado</span>
        <SegmentedControl
          ariaLabel="Formato de salida"
          value={format}
          onChange={setFormat}
          options={[
            { value: '9:16', label: '9:16' },
            { value: '1:1', label: '1:1' },
            { value: '16:9', label: '16:9' },
          ]}
        />
        <SegmentedControl
          ariaLabel="Resolución"
          value={resolution}
          onChange={setResolution}
          options={[
            { value: '720p', label: '720p' },
            { value: '1080p', label: '1080p' },
            { value: '4K', label: '4K', lockedReason: 'Disponible en el plan Pro' },
          ]}
        />
        <span className={styles.label}>Interruptor</span>
        <Switch checked={faces} onChange={setFaces} label="Seguir caras" />
        <Switch checked={bypass} onChange={setBypass} label="Renderizar sin revisar" description="El proyecto se detiene en la revisión cuando está apagado." />
        <Switch checked={false} onChange={() => {}} label="SEO: título, texto y hashtags" disabled />
        <span className={styles.label}>Tilde</span>
        <Checkbox checked={checked} onChange={(event) => setChecked(event.target.checked)} label="Incluir «Atardecer en Potrerillos»" />
        <Checkbox label="Acepto los términos" />
        <Checkbox label="Algunos seleccionados" indeterminate />
        <span className={styles.label}>Radio</span>
        <RadioGroup ariaLabel="Nivel de IA" value={level} onChange={setLevel} layout="row">
          <Radio value="fast" label="Rápido" />
          <Radio value="smart" label="Inteligente" />
        </RadioGroup>
        <Field label="Cantidad máxima" hint="Contador: de 1 a 20.">
          <Stepper value={clips} onChange={setClips} min={1} max={20} decrementLabel="Un clip menos" incrementLabel="Un clip más" />
        </Field>
        <Field label="Zoom de la cámara">
          <Slider value={zoom} onChange={setZoom} min={1} max={4} step={0.1} formatValue={(value) => `${value.toFixed(1).replace('.', ',')}×`} />
        </Field>
      </Card>
      <Card padded className={styles.col} style={{ gridColumn: '1 / -1' }}>
        <span className={styles.label}>Tarjeta de opción</span>
        <RadioGroup ariaLabel="Estilo de edición" value={style} onChange={setStyle} layout="cards" columns={4}>
          <RadioCard value="dynamic" title="Dinámico" description="Cortes rápidos y zoom." />
          <RadioCard value="minimalist" title="Minimalista" description="Limpio, para podcast." />
          <RadioCard value="vlog" title="Vlog" description="Cálido y cercano." />
          <RadioCard value="hormozi" title="Hormozi" description="Subtítulos grandes." lockedReason="Disponible en el plan Creador" />
        </RadioGroup>
        <span className={styles.label}>Zona para soltar archivos</span>
        <Field label="Video" hint={file ? `Elegiste ${file}` : 'MP4, MOV o AVI, hasta 500 MB.'}>
          <FileDropZone accept=".mp4,.mov,.avi" onFiles={(files) => setFile(files[0]?.name ?? null)} title="Arrastrá tu video acá" description="o elegilo desde tu computadora" inputLabel="Elegir video" />
        </Field>
      </Card>
    </div>
  );
}

function Statuses() {
  const [filter, setFilter] = useState('all');
  return (
    <>
      <div className={styles.row}>
        <Badge dot>En cola</Badge>
        <Badge tone="info" dot>
          Transcribiendo
        </Badge>
        <Badge tone="warn" dot>
          En espera
        </Badge>
        <Badge tone="accent" dot>
          Listo para revisar
        </Badge>
        <Badge tone="info" dot>
          Renderizando
        </Badge>
        <Badge tone="ok" dot>
          Listo
        </Badge>
        <Badge tone="warn" dot>
          Listo con errores
        </Badge>
        <Badge tone="err" dot>
          Error
        </Badge>
      </div>
      <div className={styles.row}>
        {(Object.keys(POST_STATUS) as Array<keyof typeof POST_STATUS>).map((status) => (
          <Badge key={status} tone={POST_STATUS[status].tone} icon={POST_STATUS[status].icon ?? undefined}>
            {POST_STATUS[status].label}
          </Badge>
        ))}
      </div>
      <div className={styles.row}>
        {(Object.keys(TRANSACTION_KIND) as Array<keyof typeof TRANSACTION_KIND>).map((kind) => (
          <Badge key={kind} tone={TRANSACTION_KIND[kind].tone} dot>
            {TRANSACTION_KIND[kind].label}
          </Badge>
        ))}
      </div>
      <div className={styles.row}>
        <Badge tone="ok" dot size="sm">
          Listo
        </Badge>
        <Badge tone="info" icon="pub-scheduled" size="sm">
          Programado
        </Badge>
        <Badge tone="outline" size="sm">
          Pro
        </Badge>
        <Badge tone="outline">Por defecto</Badge>
        <Badge tone="outline">Tu plan</Badge>
        <Badge tone="accent" icon="sparkles">
          Sugerido por IA
        </Badge>
        <StatusDot tone="info" live label="En curso" />
        <StatusDot tone="ok" label="Listo" />
        <StatusDot tone="err" label="Error" />
      </div>
      <FilterChips
        ariaLabel="Filtrar por estado"
        value={filter}
        onChange={setFilter}
        options={[
          { value: 'all', label: 'Todos', count: 8 },
          { value: 'in_progress', label: 'En proceso', count: 3 },
          { value: 'to_review', label: 'Para revisar', count: 1 },
          { value: 'ready', label: 'Listos', count: 3 },
          { value: 'failed', label: 'Con error', count: 1, hideWhenEmpty: true },
          { value: 'published', label: 'Publicados', count: 3, icon: 'pub-published' },
        ]}
      />
      <div className={styles.col}>
        <ProgressBar label="Subiendo" caption="Subiendo" value={64} valueText="64 %" />
        <ProgressBar label="Renderizando" caption="Renderizando" value={4} max={8} valueText="4 de 8" />
        <ProgressBar label="Clips revisados" caption="Revisados" tone="accent" value={6} max={9} valueText="6 de 9" />
        <div className={styles.row}>
          <span className="t-small">Transcribiendo</span>
          <StepSegments done={1} />
          <span className="num t-small muted">Paso 2 de 4</span>
          <StepSegments done={4} running={false} />
          <span className="num t-small muted">Listo en 8:17</span>
        </div>
        <div className={styles.row}>
          <Score value={92} />
          <Score value={76} />
          <Score value={58} />
          <Spinner label="Cargando" />
        </div>
      </div>
      <Card>
        <SkeletonRows rows={2} />
      </Card>
      <div className={styles.row}>
        <Skeleton width={160} height={90} />
        <SkeletonText lines={3} />
      </div>
    </>
  );
}

function Notices() {
  return (
    <div className={styles.grid2}>
      <Banner tone="info" title="Incluye 10 % de descuento en tokens">
        Es parte del plan Pro y ya está aplicado en el costo.
      </Banner>
      <Banner tone="warn" title="En espera">
        Hay mucha demanda. Tu video sigue en la fila y arranca solo; no tenés que hacer nada.
      </Banner>
      <Banner tone="err" title="No encontramos voz en el video">
        OneCreator elige clips a partir de lo que se dice. Te devolvimos <Tokens value={1755} />.
      </Banner>
      <Banner tone="ok" title="Tu email quedó verificado">
        Ya podés crear tu primer proyecto.
      </Banner>
      <Banner title="Tu plan agrega marca de agua">
        Pasate al plan Creador para sacarla.{' '}
        <Link className="lnk" href="/uso/planes">
          Ver planes
        </Link>
      </Banner>
      <Banner
        tone="err"
        title="No pudimos conectar"
        action={
          <Button size="sm" variant="secondary" iconLeft="retry">
            Reintentar
          </Button>
        }
      />
      <Card padded className={styles.col}>
        <span className={styles.label}>Bloqueado por plan</span>
        <Switch checked={false} onChange={() => {}} label="SEO: título, texto y hashtags" disabled />
        <PlanLock plan="Creador" />
      </Card>
      <Card padded className={styles.col}>
        <span className={styles.label}>Bloqueado por rol</span>
        <div className={styles.row}>
          <Button variant="primary" iconLeft="plus" disabledReason="Pedile acceso a un admin">
            Nuevo proyecto
          </Button>
          <Tooltip content="Pedile acceso a un admin">
            <Button variant="secondary" disabled>
              Publicar
            </Button>
          </Tooltip>
        </div>
      </Card>
    </div>
  );
}

function Identity() {
  return (
    <>
      <div className={styles.row}>
        <Avatar name="Lucía Fernández" />
        <Avatar name="Martín Sosa" />
        <Avatar name="Carla Ruiz" size="sm" />
        <Avatar name="Diego Paz" size="lg" />
        <Avatar name="Estudio Norte" kind="workspace" />
        <Avatar name="Canal de Martín" kind="workspace" size="sm" />
        <Avatar name="Carla Ruiz" size="sm" showName />
      </div>
      <div className={styles.row}>
        <NetworkChip platform="TIKTOK" />
        <NetworkChip platform="INSTAGRAM_REELS" />
        <NetworkChip platform="YOUTUBE_SHORTS" />
        <NetworkChip platform="TIKTOK" size="sm" short />
        <NetworkChip platform="INSTAGRAM_REELS" hideName />
      </div>
      <div className={styles.row} style={{ alignItems: 'flex-end' }}>
        <VideoThumb ratio="16:9" seed="a" duration={4360} width={160} />
        <VideoThumb ratio="16:9" seed="b" duration={1124} width={96} />
        <VideoThumb ratio="9:16" seed="c" duration={31} score={91} width={104} captions="#f2c94c" />
        <VideoThumb ratio="9:16" seed="d" duration={44} width={72} />
        <VideoThumb ratio="1:1" seed="e" duration={52} width={96} />
        <VideoThumb ratio="9:16" seed="f" xs width={27} />
      </div>
    </>
  );
}

function Structure() {
  const [tab, setTab] = useState<'week' | 'month' | 'list'>('week');
  const [page, setPage] = useState(1);
  return (
    <>
      <div className={styles.box}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: 56, padding: '0 24px', borderBottom: '1px solid var(--border)' }}>
          <Brand />
          <span className="muted">/</span>
          <Avatar name="Estudio Norte" kind="workspace" size="sm" showName />
          <Badge tone="outline" size="sm">
            Pro
          </Badge>
          <span style={{ flex: 1 }} />
          <Kbd>⌘K</Kbd>
          <ThemeToggle />
        </div>
        <NavTabs
          variant="main"
          ariaLabel="Secciones (muestra)"
          items={[
            { href: '/dev/kit', label: 'Proyectos' },
            { href: '/publicaciones', label: 'Publicaciones' },
            { href: '/brand-kits', label: 'Brand kits' },
            { href: '/uso', label: 'Uso y facturación' },
            { href: '/ajustes', label: 'Ajustes' },
          ]}
        />
      </div>
      <PageHead
        title="Proyectos"
        description="8 proyectos · recientes primero"
        actions={
          <Button variant="primary" iconLeft="plus">
            Nuevo proyecto
          </Button>
        }
      />
      <ProjectHeader
        crumbs={[{ label: 'Proyectos', href: '/proyectos' }, { label: 'Vlog Mendoza' }]}
        thumb={<VideoThumb ratio="16:9" seed="vlog" duration={1124} width="100%" />}
        title="Vlog Mendoza"
        badge={
          <Badge tone="ok" dot>
            Listo
          </Badge>
        }
        statusText="6 clips listos."
        meta={[
          { icon: 'file', content: 'Archivo' },
          { icon: 'clock', content: <Duration seconds={1124} /> },
          { content: <Avatar name="Carla Ruiz" size="sm" showName /> },
          { icon: 'calendar', content: <DateTime value={NOW} /> },
          { icon: 'tokens', content: <Tokens value={763.2} /> },
        ]}
        actions={
          <>
            <Button variant="primary">Ver clips</Button>
            <IconButton icon="more" label="Más acciones" />
          </>
        }
      />
      <NavTabs
        ariaLabel="Secciones del proyecto (muestra)"
        items={[
          { href: '/dev/kit', label: 'Resumen' },
          { href: '/dev/kit/clips', label: 'Clips', count: 6 },
          { href: '/dev/kit/transcripcion', label: 'Transcripción' },
          { href: '/dev/kit/actividad', label: 'Actividad' },
        ]}
      />
      <ProjectHeader
        compact
        crumbs={[{ label: 'Proyectos', href: '/proyectos' }, { label: 'Vlog Mendoza', href: '/proyectos' }, { label: 'Editar clip' }]}
        title="Atardecer en Potrerillos"
        badge={
          <Badge tone="ok" dot>
            Listo
          </Badge>
        }
        meta={[{ content: '0:31 · 1080p' }]}
        actions={<Button variant="secondary">Descargar</Button>}
      />
      <TabList
        ariaLabel="Vista"
        idBase="kit-view"
        value={tab}
        onChange={setTab}
        items={[
          { value: 'week', label: 'Semana' },
          { value: 'month', label: 'Mes' },
          { value: 'list', label: 'Lista', count: 8 },
        ]}
      />
      <TabPanel idBase="kit-view" value={tab} active>
        <p className="t-small muted">Panel de la pestaña «{tab}» (pestañas en la página, sin cambiar de ruta).</p>
      </TabPanel>
      <Crumbs items={[{ label: 'Brand kits', href: '/brand-kits' }, { label: 'Estudio Norte' }]} />
      <div className={styles.box}>
        <div style={{ padding: '20px 24px' }}>
          <SkeletonText />
        </div>
        <ActionBar
          summary={
            <>
              <span className="num">6</span> clips seleccionados · <span className="num">4:20</span> · <Tokens value={117} />
            </>
          }
          detail="Incluye 10 % de descuento del plan Pro"
        >
          <Button variant="ghost">Cancelar</Button>
          <Button variant="accent" size="lg">
            Renderizar 6 clips
          </Button>
        </ActionBar>
      </div>
      <Card>
        <Pagination page={page} pageSize={25} count={64} onPageChange={setPage} noun="proyectos" />
      </Card>
      <SettingsGroup title="Workspace" description="Lo ven todos los miembros.">
        <SettingsRow label="Nombre del workspace" description="Aparece en el selector y en las invitaciones." htmlFor="kit-ws-name">
          <Input id="kit-ws-name" defaultValue="Estudio Norte" />
          <Button variant="secondary">Guardar</Button>
        </SettingsRow>
        <SettingsRow label="Apariencia" description="Oscuro por defecto; Sistema sigue a tu dispositivo.">
          <ThemePreferenceControl />
        </SettingsRow>
      </SettingsGroup>
      <SettingsGroup title="Zona de peligro" danger>
        <SettingsRow label="Borrar el workspace" description="Se borran los proyectos, los clips y el saldo. No se puede deshacer.">
          <Button variant="danger">Borrar workspace</Button>
        </SettingsRow>
      </SettingsGroup>
    </>
  );
}

function DataDisplay() {
  return (
    <>
      <Card>
        <Table caption="Movimientos">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Concepto</th>
              <th>Tipo</th>
              <th className="right">Monto</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="num t-small">
                <DateTime value={NOW} />
              </td>
              <td>
                <Link className="lnk" href="/proyectos">
                  Clase 3: Finanzas personales
                </Link>
              </td>
              <td>
                <Badge tone="warn" dot>
                  Reservado
                </Badge>
              </td>
              <td className="right">
                <Tokens value={-1404.9} unit={false} signed />
              </td>
            </tr>
            <tr>
              <td className="num t-small">
                <DateTime value={new Date(NOW.getTime() - 3 * 86400000)} />
              </td>
              <td>Webinar de lanzamiento</td>
              <td>
                <Badge tone="ok" dot>
                  Devolución
                </Badge>
              </td>
              <td className="right">
                <Tokens value={1755} unit={false} signed />
              </td>
            </tr>
          </tbody>
        </Table>
      </Card>
      <Card>
        <ListRow href="/proyectos">
          <VideoThumb ratio="16:9" seed="pod" duration={4360} width={96} />
          <ListRowMain>
            <span style={{ fontWeight: 500 }}>Podcast Ep. 42 — Cómo vender sin vender</span>
            <span className="t-small muted">
              Archivo · <span className="num">9</span> clips · Lucía · hace 28 min
            </span>
          </ListRowMain>
          <Badge tone="accent" dot>
            Listo para revisar
          </Badge>
        </ListRow>
        <ListRow>
          <VideoThumb ratio="16:9" seed="stream" duration={10090} width={96} />
          <ListRowMain>
            <span style={{ fontWeight: 500 }}>Stream Valorant del viernes</span>
            <span className="t-small muted">Link de YouTube · 8 clips · Martín · hace 40 min</span>
          </ListRowMain>
          <Badge tone="info" dot>
            Renderizando
          </Badge>
        </ListRow>
      </Card>
      <div className={styles.grid2}>
        <Kpi label="Saldo disponible" value="4.860" unit="tokens" hint="Lo que podés usar ahora." />
        <Kpi label="Reservado" value="2.617,65" unit="tokens" hint="Se cobra cuando termine cada trabajo." />
      </div>
      <Card padded>
        <KeyValue
          items={[
            { label: 'Resolución', value: '1080p' },
            { label: 'Formato', value: '9:16' },
            { label: 'SEO', value: <>Sí · <Tokens value={5} /></> },
            { label: 'Brand kit', value: 'Estudio Norte' },
          ]}
        />
      </Card>
      <CostBreakdown
        lines={[
          { label: 'Duración total', value: '4:20' },
          { label: 'Base por resolución', note: '260 s × 0,5 × 1 (720p)', value: '130' },
          { label: 'Descuento del plan Pro', note: '−10 %', value: '−13' },
          { kind: 'total', label: 'Total', value: '117 tokens' },
          { kind: 'after', label: 'Saldo después', value: '4.860 → 4.743 tokens' },
        ]}
      />
      <div className={styles.row}>
        <Tokens value={120} />
        <Tokens value={12.5} />
        <Tokens value={1404.9} signed />
        <Duration seconds={3932} />
        <Duration seconds={41} />
        <Timecode seconds={211} />
        <Timecode seconds={211.5} tenths />
        <DateTime value={NOW} />
        <DateTime value={NOW} format="date" />
        <DateTime value={new Date(Date.now() - 12 * 60000)} format="relative" />
      </div>
    </>
  );
}

function Clips() {
  return (
    <>
      <div className={styles.grid6}>
        <ClipCard
          seed="c1"
          title="Atardecer en Potrerillos"
          duration={31}
          score={91}
          meta={
            <Badge tone="ok" dot size="sm">
              Listo
            </Badge>
          }
        />
        <ClipCard
          seed="c2"
          title="Subimos a 3.000 metros en una hora"
          duration={44}
          score={87}
          meta={
            <>
              <Badge tone="ok" dot size="sm">
                Publicado
              </Badge>
              <NetworkChip platform="TIKTOK" size="sm" short />
            </>
          }
        />
        <ClipCard
          seed="c3"
          title="El asado más alto del mundo"
          duration={38}
          score={82}
          meta={
            <Badge tone="info" icon="pub-scheduled" size="sm">
              Programado
            </Badge>
          }
        />
        <ClipCard seed="c4" title="Lo que nadie te cuenta del cruce" state="rendering" />
        <ClipCard
          seed="c5"
          title="Camino a Uspallata"
          state="failed"
          failedAction={
            <Button size="sm" variant="secondary" iconLeft="retry">
              Reintentar
            </Button>
          }
          meta={
            <Badge tone="err" dot size="sm">
              Error
            </Badge>
          }
        />
        <ClipCard seed="c6" size="sm" title="Tarjeta chica para la tira del resumen" duration={27} score={74} />
      </div>
      <Card>
        <StageStepper
          steps={[
            { key: 'prep', label: 'Preparar', state: 'done', detail: 'Terminado · empezó 14:18:06', duration: '0:42' },
            { key: 'transcribe', label: 'Transcribir', state: 'run', detail: 'En curso · Paso 2 de 4', duration: '1:15' },
            { key: 'moderate', label: 'Revisar contenido', state: 'wait', detail: 'En espera. Hay mucha demanda; arranca solo.' },
            { key: 'select', label: 'Elegir clips', state: 'fail', detail: 'Falló. No encontramos momentos que funcionen como clips.', duration: '1:05' },
            { key: 'render', label: 'Renderizar', state: 'pending', separatorBefore: 'Después de tu revisión' },
          ]}
        />
      </Card>
      <EmptyState
        icon="calendar"
        title="Todavía no programaste nada"
        description="Elegí un clip listo y decidí cuándo sale."
        action={<Button variant="primary">Ir a mis clips</Button>}
      />
    </>
  );
}

function Floating() {
  const toast = useToast();
  const billing = useBillingDialogs();
  const [dialog, setDialog] = useState<string | null>(null);
  const close = () => setDialog(null);
  return (
    <>
      <div className={styles.row}>
        <DropdownMenu
          ariaLabel="Acciones del proyecto"
          align="start"
          trigger={(props) => (
            <Button variant="secondary" iconRight="chevron-down" {...props}>
              Menú de acciones
            </Button>
          )}
          items={[
            { key: 'open', label: 'Abrir', icon: 'external' },
            { key: 'rename', label: 'Renombrar', icon: 'edit' },
            { key: 'dup', label: 'Duplicar configuración', icon: 'copy' },
            { key: 'locked', label: 'Publicar', icon: 'send', disabled: true, disabledReason: 'Pedile acceso a un admin' },
            { key: 'delete', label: 'Borrar', icon: 'trash', danger: true, separatorBefore: true },
          ]}
        />
        <Popover
          ariaLabel="Filtros"
          trigger={(props) => (
            <Button variant="secondary" iconLeft="filter" {...props}>
              Popover
            </Button>
          )}
        >
          <div style={{ padding: 16, width: 260 }} className={styles.col}>
            <p className="t-label muted">Filtrar por estado</p>
            <Checkbox label="En proceso" defaultChecked />
            <Checkbox label="Para revisar" />
          </div>
        </Popover>
        <Tooltip content="4.860 disponibles · 2.617,65 reservados">
          <Button variant="ghost">Tooltip</Button>
        </Tooltip>
      </div>
      <div className={styles.row}>
        <Button onClick={() => setDialog('sm')}>Diálogo chico</Button>
        <Button onClick={() => setDialog('md')}>Diálogo mediano</Button>
        <Button onClick={() => setDialog('lg')}>Diálogo grande</Button>
        <Button onClick={() => setDialog('drawer')}>Panel lateral</Button>
      </div>
      <div className={styles.row}>
        <Button onClick={() => setDialog('cost')}>D1 · Confirmar costo</Button>
        <Button onClick={() => billing.showInsufficientBalance({ required: 1966.5, concept: 'Costo del proyecto' })}>D2 · Saldo insuficiente</Button>
        <Button onClick={() => billing.showPlanLimit({ kind: 'duration', onChangeOptions: () => toast.show('Volvés al formulario') })}>D3 · Límite del plan</Button>
        <Button onClick={() => setDialog('delete')}>Confirmar borrado</Button>
        <Button onClick={() => setDialog('typed')}>Borrar escribiendo el nombre</Button>
        <Button onClick={() => setDialog('blocked')}>D5 · Contenido bloqueado</Button>
        <Button onClick={() => setDialog('network')}>D6 · Conectar red</Button>
        <Button onClick={() => setDialog('unsaved')}>D8 · Cambios sin guardar</Button>
        <Button onClick={() => notifySessionExpired()}>D9 · Sesión vencida</Button>
      </div>
      <div className={styles.row}>
        <Button onClick={() => toast.success('Clip descargado')}>Toast de confirmación</Button>
        <Button onClick={() => toast.success('Programado: 26 nov 2026, 18:00', { action: { label: 'Ver', href: '/publicaciones' } })}>Toast con acción</Button>
        <Button onClick={() => toast.error('No pudimos descargar el clip', { action: { label: 'Reintentar' } })}>Toast de error</Button>
      </div>

      <Dialog
        open={dialog === 'sm' || dialog === 'md' || dialog === 'lg'}
        onClose={close}
        size={dialog === 'sm' ? 'sm' : dialog === 'lg' ? 'lg' : 'md'}
        title="Renombrar proyecto"
        description="El nombre se ve en la lista y en las publicaciones."
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Cancelar
            </Button>
            <Button variant="primary" onClick={close}>
              Guardar
            </Button>
          </>
        }
      >
        <Field label="Título">
          <Input defaultValue="Vlog Mendoza" data-autofocus />
        </Field>
      </Dialog>
      <Drawer
        open={dialog === 'drawer'}
        onClose={close}
        title="Publicar clip"
        description="Atardecer en Potrerillos"
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Cancelar
            </Button>
            <Button variant="primary" onClick={close}>
              Programar
            </Button>
          </>
        }
      >
        <Field label="Texto" hint={<span className="num">58 / 2.200</span>}>
          <Textarea defaultValue="Subimos a 3.000 metros en una hora y esto fue lo que pasó." />
        </Field>
        <NetworkChip platform="TIKTOK" />
      </Drawer>
      <ConfirmCostDialog
        open={dialog === 'cost'}
        onClose={close}
        onConfirm={close}
        title="Renderizar 6 clips"
        subtitle="Podcast Ep. 42 — Cómo vender sin vender"
        durationSeconds={260}
        resolution="720p"
        estimate={{ base: 130, multiplier: 1, addOns: 0, discountRate: 0.1, discount: 13, total: 117 }}
        planName="Pro"
        balance={4860}
      />
      <ConfirmDialog
        open={dialog === 'delete'}
        onClose={close}
        onConfirm={close}
        tone="danger"
        title="¿Quitar a Carla Ruiz?"
        description="Pierde el acceso a Estudio Norte. Sus proyectos quedan en el workspace."
        confirmLabel="Quitar miembro"
      />
      <ConfirmDialog
        open={dialog === 'typed'}
        onClose={close}
        onConfirm={close}
        tone="danger"
        title="¿Borrar Estudio Norte?"
        description="Se borran los proyectos, los clips, las publicaciones y el saldo. No se puede deshacer."
        confirmLabel="Borrar workspace"
        typeToConfirm={{ text: 'Estudio Norte', label: 'Escribí «Estudio Norte» para confirmar' }}
      />
      <ContentBlockedDialog open={dialog === 'blocked'} onClose={close} />
      <UnsavedChangesDialog open={dialog === 'unsaved'} onStay={close} onDiscard={close} what="el recorte y el título nuevos de «Atardecer en Potrerillos»" />
      <ConnectNetworkDialog platform={dialog === 'network' ? 'TIKTOK' : null} onClose={close} />
    </>
  );
}

/** /dev/kit: every component of the design system, in both themes, for review. */
export default function KitPage() {
  return (
    <div className={styles.page}>
      <header className={styles.cover}>
        <div>
          <p className="t-over">Sistema visual · kit v2.3</p>
          <h1 className="t-h1">Kit de componentes</h1>
          <p className="muted" style={{ maxWidth: 640 }}>
            Todos los componentes de <span className="code">@/ui</span>, <span className="code">@/dialogs</span> y <span className="code">@/shell</span>, en tema oscuro y claro. Los
            flotantes (diálogos, panel, toasts) se abren con el tema de la página: cambialo con el botón.
          </p>
        </div>
        <div className={styles.row}>
          <ThemeToggle />
          <Button variant="secondary" href="/proyectos">
            Ir a la app
          </Button>
        </div>
      </header>
      <nav className={styles.toc} aria-label="Secciones del kit">
        {SECTIONS.map(([id, label]) => (
          <a key={id} href={`#${id}`}>
            {label}
          </a>
        ))}
      </nav>

      <Section id="escenarios" n="00" title="Escenarios" note="Datos de prueba: elegí uno y la app se recarga con ese estado.">
        {API_MOCKS ? (
          <Card>
            <ScenarioPicker />
          </Card>
        ) : (
          <Banner tone="info" title="Los mocks están apagados">
            La app está hablando con el backend real (<span className="code">NEXT_PUBLIC_API_MOCKS=false</span>).
          </Banner>
        )}
      </Section>

      <Section id="color" n="01" title="Color" note="Los dos temas, lado a lado. Oscuro es el tema por defecto.">
        <Duo>
          <div className={styles.swatches}>
            {TOKENS.map((token) => (
              <div key={token}>
                <div className={styles.swatch} style={{ background: `var(${token})` }} />
                <span className="code t-label">{token}</span>
              </div>
            ))}
          </div>
        </Duo>
      </Section>

      <Section id="tipografia" n="02" title="Tipografía y números" note="Geist Sans para todo; Geist Mono solo para tiempos de edición y atajos.">
        <Duo>
          <p className="t-display">Subí tu video</p>
          <p className="t-h1">Proyectos</p>
          <p className="t-h2">Podcast Ep. 42 — Cómo vender sin vender</p>
          <p className="t-h3">Resumen de la compra</p>
          <p className="t-body">Revisá los clips antes de renderizar.</p>
          <p className="t-small muted">Hay mucha demanda. Arranca solo.</p>
          <p className="t-label">Etiqueta de campo</p>
          <p className="t-over">Sobre-título</p>
          <div className={styles.row}>
            <span className="num">1.404,90 tokens</span>
            <span className="num">24 nov 2026, 14:18</span>
            <span className="num">1:05:32</span>
            <span className="timecode">03:31–04:02</span>
            <Kbd>I</Kbd>
            <Kbd>O</Kbd>
            <Kbd>⌘K</Kbd>
          </div>
        </Duo>
      </Section>

      <Section id="botones" n="03" title="Botones" note="El primario es monocromo y hay uno solo por pantalla. El acento es para «Revisar clips» y «Renderizar».">
        <Duo stack>
          <Buttons />
        </Duo>
      </Section>

      <Section id="campos" n="04" title="Campos y controles" note="Etiqueta arriba, ayuda o error debajo.">
        <Duo stack>
          <Controls />
        </Duo>
      </Section>

      <Section id="estado" n="05" title="Estado y progreso" note="Etiquetas con punto para proyecto, clip y billetera; con ícono para publicaciones. Lo que está en curso va en azul.">
        <Duo>
          <Statuses />
        </Duo>
      </Section>

      <Section id="avisos" n="06" title="Avisos y bloqueos" note="Un ícono por tono. Lo que el plan o el rol no permite se muestra igual.">
        <Duo stack>
          <Notices />
          <FixedBanner icon="offline" pulsing>
            Sin conexión. Reintentando
          </FixedBanner>
          <FixedBanner icon="wrench" tone="info">
            Mantenimiento programado hoy de 02:00 a 03:30.
          </FixedBanner>
        </Duo>
      </Section>

      <Section id="identidad" n="07" title="Avatares, redes y miniaturas" note="Personas en círculo, workspaces en cuadrado. Redes con un glifo genérico y el nombre al lado.">
        <Duo>
          <Identity />
        </Duo>
      </Section>

      <Section id="estructura" n="08" title="Estructura" note="Barra superior, encabezados, pestañas, barra de acción, paginación y filas de ajustes.">
        <Duo stack>
          <Structure />
        </Duo>
      </Section>

      <Section id="datos" n="09" title="Tablas y datos" note="Tabla, fila de proyecto, tarjeta de dato, clave y valor, costo y formatos.">
        <Duo stack>
          <DataDisplay />
        </Duo>
      </Section>

      <Section id="clips" n="10" title="Clip y proceso" note="La tarjeta de clip en sus estados, los pasos del proceso y el estado vacío.">
        <Duo stack>
          <Clips />
        </Duo>
      </Section>

      <Section id="flotantes" n="11" title="Menús, diálogos y toasts" note="Menú, popover y tooltip; diálogos con trampa de foco y Escape; panel lateral; toasts de 5 s, dos como máximo.">
        <Duo stack>
          <Floating />
        </Duo>
      </Section>

      <Section id="video" n="12" title="Reproductor" note="Espacio reproduce o pausa, ← → mueven 5 s, I y O marcan inicio y fin. Sin video muestra el fotograma de muestra.">
        <Duo>
          <VideoPlayer label="Reproductor de muestra" seed="player" ratio="16:9" duration={95} />
        </Duo>
      </Section>

      <Section id="sistema" n="13" title="Sistema" note="Una frase, una acción y nada más.">
        <div className={styles.grid2}>
          <Card>
            <NotFoundMessage inline />
          </Card>
          <Card>
            <NoAccessMessage inline />
          </Card>
          <Card>
            <ServerErrorMessage inline onRetry={() => {}} />
          </Card>
          <Card>
            <CardHead title="Tarjeta con cabecera y pie" description="Para agrupar contenido." actions={<Button size="sm">Acción</Button>} />
            <div style={{ padding: 20 }}>
              <SkeletonText lines={3} />
            </div>
            <CardFoot>
              <span className="t-small muted">Pie de tarjeta</span>
              <Button size="sm" variant="primary">
                Guardar
              </Button>
            </CardFoot>
          </Card>
        </div>
      </Section>

      <Section id="iconos" n="14" title="Íconos" note="Un solo set de línea, 24 × 24, trazo redondeado. Se usan a 14, 16 y 20 px.">
        <div className={styles.icons}>
          {(Object.keys(ICON_PATHS) as IconName[]).map((name) => (
            <div key={name} className={styles.icon}>
              <Icon name={name} size={20} />
              <span className="code">{name}</span>
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}
