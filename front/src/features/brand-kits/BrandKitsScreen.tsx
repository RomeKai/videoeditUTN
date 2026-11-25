'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import type { BrandKit } from '@/api/contracts/brandkits';
import { useBrandKits, useDeleteBrandKit, useDuplicateBrandKit, useSetDefaultBrandKit } from '@/api/hooks';
import { ConfirmDialog } from '@/dialogs';
import { errorMessage } from '@/lib/error-messages';
import { LoadError } from '@/shell/LoadError';
import { usePermissions } from '@/shell/session';
import { Badge, Button, Card, DropdownMenu, Icon, IconButton, Page, PageHead, Skeleton, Tooltip, thumbHue, useToast, type MenuItem } from '@/ui';
import styles from './BrandKits.module.css';
import { KitPreview, LogoMark } from './KitPreview';
import { fileName, fontLabel, kitColor } from './kit';

const INTRO = 'Colores, fuentes y logo para tus clips. El kit por defecto se aplica a los proyectos nuevos; podés elegir otro al crear cada proyecto.';

// The response carries whatever the backend stored: normalised before it reaches the CSS.
const colorsOf = (kit: BrandKit) => ({ text: kitColor(kit, 'primary_color'), background: kitColor(kit, 'secondary_color'), accent: kitColor(kit, 'accent_color') });

function KitCard({ kit, onDelete }: { kit: BrandKit; onDelete: (kit: BrandKit) => void }) {
  const toast = useToast();
  const { can, reason } = usePermissions();
  const duplicate = useDuplicateBrandKit();
  const setDefault = useSetDefaultBrandKit();
  const mayManage = can('brandkit.manage');
  const blocked = mayManage ? {} : { disabled: true, disabledReason: reason };
  const colors = colorsOf(kit);
  const font = fontLabel(kit.primary_font);
  const href = `/brand-kits/${kit.id}`;

  const items: MenuItem[] = [
    { key: 'edit', label: 'Editar', icon: 'edit', ...(mayManage ? { href } : blocked) },
    {
      key: 'duplicate',
      label: 'Duplicar',
      icon: 'copy',
      ...blocked,
      onSelect: () =>
        duplicate.mutate(kit.id, {
          onSuccess: (copy) => toast.success(`Se creó «${copy.name}»`, { action: { label: 'Editar', href: `/brand-kits/${copy.id}` } }),
          onError: (error) => toast.error(errorMessage(error, 'No pudimos duplicar el brand kit.')),
        }),
    },
    {
      key: 'default',
      label: 'Usar por defecto',
      icon: 'star',
      ...(kit.is_default ? { disabled: true, disabledReason: 'Ya es el kit por defecto' } : blocked),
      onSelect: () =>
        setDefault.mutate(kit.id, {
          onSuccess: () => toast.success(`«${kit.name}» es ahora el kit por defecto`),
          onError: (error) => toast.error(errorMessage(error, 'No pudimos cambiar el kit por defecto.')),
        }),
    },
    { key: 'delete', label: 'Borrar', icon: 'trash', danger: true, separatorBefore: true, ...blocked, onSelect: () => onDelete(kit) },
  ];

  return (
    <Card as="article" className={styles.kit} aria-label={`Brand kit ${kit.name}`}>
      <KitPreview className={styles.kprev} colors={colors} name={kit.name} logo={kit.watermark_logo ? { src: kit.watermark_logo } : null} position={kit.watermark_position} hue={thumbHue(kit.id)} compact />
      <div className={styles.kbody}>
        <div className={styles.khead}>
          <div className={styles.kheadText}>
            <h2 className="t-h3">
              <Link href={href}>{kit.name}</Link>
            </h2>
            {kit.description ? <p className={`t-small muted ${styles.kdesc}`}>{kit.description}</p> : null}
          </div>
          <DropdownMenu
            className={styles.kmenu}
            ariaLabel={`Acciones de ${kit.name}`}
            items={items}
            trigger={(props) => <IconButton icon="more" label={`Acciones de ${kit.name}`} {...props} />}
          />
        </div>
        <div className={styles.kmid}>
          <span
            className={styles.sws}
            role="img"
            aria-label={`Colores: Texto ${colors.text}, Fondo ${colors.background}, Palabra activa ${colors.accent}`}
          >
            <span style={{ background: colors.text }} title={`Texto: ${colors.text}`} />
            <span style={{ background: colors.background }} title={`Fondo: ${colors.background}`} />
            <span style={{ background: colors.accent }} title={`Palabra activa: ${colors.accent}`} />
          </span>
          {kit.is_default ? <Badge tone="outline">Por defecto</Badge> : null}
        </div>
        <div className={styles.kassets}>
          {kit.watermark_logo ? (
            <div className={styles.kasset}>
              <LogoMark name={kit.name} src={kit.watermark_logo} colors={colors} />
              <span className={styles.atxt}>
                <span className={`t-small ${styles.strong}`}>Logo</span>
                <span className="t-label subtle">{fileName(kit.watermark_logo)}</span>
              </span>
            </div>
          ) : (
            <div className={styles.kasset}>
              <span className={styles.logoEmpty} aria-hidden="true">
                <Icon name="brand-kit" size={16} />
              </span>
              <span className={styles.atxt}>
                <span className="t-small muted">Sin logo</span>
                <span className="t-label subtle">Sin marca de agua</span>
              </span>
            </div>
          )}
          {font ? (
            <div className={styles.kasset}>
              <span className={styles.fsample} aria-hidden="true">
                Aa
              </span>
              <span className={styles.atxt}>
                <span className={`t-small ${styles.strong}`}>{font}</span>
                <span className="t-label subtle">Fuente principal</span>
              </span>
            </div>
          ) : (
            <div className={styles.kasset}>
              <span className={`${styles.fsample} subtle`} aria-hidden="true">
                Aa
              </span>
              <span className={styles.atxt}>
                <span className="t-small muted">Sin fuente propia</span>
                <span className="t-label subtle">Se usa la del sistema</span>
              </span>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function EmptyKits() {
  const { gate } = usePermissions();
  return (
    <Card as="section" aria-labelledby="brand-kits-empty">
      <div className={styles.empty}>
        <span className={styles.newkitIco} aria-hidden="true">
          <Icon name="brand-kit" size={20} />
        </span>
        <h2 className="t-h3" id="brand-kits-empty">
          Creá tu brand kit para que todos tus clips salgan con tu marca
        </h2>
        <p className="t-body muted">
          Lo armás una vez y se aplica solo a cada proyecto nuevo. Mientras tanto, tus clips salen con el estilo de subtítulos que elijas en cada proyecto.
        </p>
        <div className={styles.emptyAction}>
          <Button variant="primary" iconLeft="plus" href="/brand-kits/nuevo" {...gate('brandkit.manage')}>
            Nuevo brand kit
          </Button>
        </div>
      </div>
      <ul className={styles.what} aria-label="Qué incluye un brand kit">
        <li>
          <span className={styles.whatIco} aria-hidden="true">
            <Icon name="captions" size={16} />
          </span>
          <span>
            <span className={`t-small ${styles.whatTitle}`}>Colores</span>
            <span className="t-small muted">Texto, fondo y palabra activa de los subtítulos.</span>
          </span>
        </li>
        <li>
          <span className={styles.whatIco} aria-hidden="true">
            Aa
          </span>
          <span>
            <span className={`t-small ${styles.whatTitle}`}>Fuentes</span>
            <span className="t-small muted">Tu tipografía, en .ttf u .otf.</span>
          </span>
        </li>
        <li>
          <span className={styles.whatIco} aria-hidden="true">
            <Icon name="brand-kit" size={16} />
          </span>
          <span>
            <span className={`t-small ${styles.whatTitle}`}>Logo</span>
            <span className="t-small muted">Como marca de agua, en la esquina que elijas.</span>
          </span>
        </li>
        <li>
          <span className={styles.whatIco} aria-hidden="true">
            <Icon name="video" size={16} />
          </span>
          <span>
            <span className={`t-small ${styles.whatTitle}`}>Intro y outro</span>
            <span className="t-small muted">Un video corto al principio y al final de cada clip.</span>
          </span>
        </li>
      </ul>
    </Card>
  );
}

function KitsSkeleton() {
  return (
    <ul className={styles.kits} aria-busy="true" aria-label="Cargando brand kits">
      {[0, 1, 2].map((item) => (
        <li key={item}>
          <Card className={styles.skelKit}>
            <Skeleton width={112} height={199} radius={8} />
            <div className={styles.skelBody}>
              <Skeleton width="60%" height={18} />
              <Skeleton width="85%" />
              <Skeleton width={108} height={24} />
              <Skeleton width="70%" height={32} />
              <Skeleton width="70%" height={32} />
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}

/** F1 · Brand kits: the workspace's kits, with create, duplicate, set as default and delete. */
export function BrandKitsScreen() {
  const router = useRouter();
  const toast = useToast();
  const kits = useBrandKits();
  const remove = useDeleteBrandKit();
  const { can, reason } = usePermissions();
  const [deleting, setDeleting] = useState<BrandKit | null>(null);
  const mayManage = can('brandkit.manage');
  const list = kits.data;

  const closeDelete = () => {
    setDeleting(null);
    remove.reset();
  };

  const newTile = (
    <button
      type="button"
      className={styles.newkit}
      aria-disabled={!mayManage || undefined}
      onClick={() => {
        if (mayManage) router.push('/brand-kits/nuevo');
      }}
    >
      <span className={styles.newkitIco} aria-hidden="true">
        <Icon name="plus" size={20} />
      </span>
      <span className="t-h3">Nuevo brand kit</span>
      <span className="t-small muted">Empezá de cero o duplicá uno desde su menú.</span>
    </button>
  );

  return (
    <Page>
      <PageHead title="Brand kits" description={<span className={styles.intro}>{INTRO}</span>} />
      {kits.isError ? <LoadError error={kits.error} onRetry={() => void kits.refetch()} /> : null}
      {kits.isPending ? (
        <KitsSkeleton />
      ) : list && list.length === 0 ? (
        <EmptyKits />
      ) : list ? (
        <ul className={styles.kits}>
          {list.map((kit) => (
            <li key={kit.id}>
              <KitCard kit={kit} onDelete={setDeleting} />
            </li>
          ))}
          <li>{mayManage ? newTile : <Tooltip content={reason}>{newTile}</Tooltip>}</li>
        </ul>
      ) : null}

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={closeDelete}
        tone="danger"
        icon="trash"
        title={`¿Borrar «${deleting?.name ?? ''}»?`}
        description={
          deleting?.is_default
            ? 'Es tu kit por defecto: los proyectos nuevos van a salir sin brand kit hasta que elijas otro. Los proyectos que lo usaban quedan sin kit; los clips ya renderizados no cambian.'
            : 'Los proyectos que lo usaban quedan sin brand kit. Los clips ya renderizados no cambian.'
        }
        confirmLabel="Borrar brand kit"
        loading={remove.isPending}
        error={remove.isError ? errorMessage(remove.error, 'No pudimos borrar el brand kit.') : undefined}
        onConfirm={() => {
          if (!deleting) return;
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast.success(`Se borró «${deleting.name}»`);
              closeDelete();
            },
          });
        }}
      />
    </Page>
  );
}
