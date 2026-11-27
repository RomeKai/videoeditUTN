import type { BrandKit } from '../../contracts/brandkits';
import * as contract from '../../contracts/brandkits';
import { newId, type MockDb } from '../db';
import { badRequest, notFound, route } from '../http';
import { FILE_RULES } from '@/features/brand-kits/kit';

const FILE_FIELDS = ['primary_font', 'secondary_font', 'watermark_logo', 'intro_video', 'outro_video'] as const;
const FILE_DIR: Record<(typeof FILE_FIELDS)[number], string> = {
  primary_font: 'assets/fonts',
  secondary_font: 'assets/fonts',
  watermark_logo: 'assets/logos',
  intro_video: 'assets/videos',
  outro_video: 'assets/videos',
};
const HEX = /^#[0-9a-fA-F]{6}$/;

function own(db: MockDb, id: string | undefined, workspaceId: string): BrandKit {
  const kit = db.brandKits.find((item) => item.id === id && item.workspace === workspaceId);
  if (!kit) throw notFound();
  return kit;
}

/** Applies a multipart form to a kit, with the serializer's validations. */
function apply(kit: BrandKit, form: FormData, { partial }: { partial: boolean }): void {
  const name = form.get('name');
  if (typeof name === 'string') {
    if (!name.trim()) throw badRequest({ name: ['Este campo no puede estar en blanco.'] });
    if (name.length > 100) throw badRequest({ name: ['Asegurate de que este campo no tenga más de 100 caracteres.'] });
    kit.name = name.trim();
  } else if (!partial) {
    throw badRequest({ name: ['Este campo es obligatorio.'] });
  }
  const description = form.get('description');
  if (typeof description === 'string') kit.description = description;
  for (const field of ['primary_color', 'secondary_color', 'accent_color'] as const) {
    const value = form.get(field);
    if (typeof value !== 'string') continue;
    if (!HEX.test(value)) throw badRequest({ [field]: ['Ingresá un color hexadecimal, como #FFFFFF.'] });
    kit[field] = value.toUpperCase();
  }
  for (const field of FILE_FIELDS) {
    const value = form.get(field);
    if (value === null) continue;
    if (typeof value === 'string') {
      // An empty value clears the file.
      if (value === '') kit[field] = null;
      continue;
    }
    const file = value as File;
    // Each file's cap, as the backend checks it (back/apps/core/upload_limits.py · BRAND_KIT_FILE_MAX_BYTES).
    if (file.size > FILE_RULES[field].maxBytes) throw badRequest({ [field]: [FILE_RULES[field].tooBig] });
    if ((field === 'primary_font' || field === 'secondary_font') && !/\.(ttf|otf)$/i.test(file.name)) {
      throw badRequest({ [field]: ['Subí una fuente .ttf u .otf.'] });
    }
    if (field === 'watermark_logo' && !/\.png$/i.test(file.name)) throw badRequest({ [field]: ['Subí una imagen PNG.'] });
    kit[field] = `/media/${FILE_DIR[field]}/${file.name}`;
  }
  const position = form.get('watermark_position');
  if (typeof position === 'string') {
    const parsed = contract.WatermarkPosition.safeParse(position);
    if (!parsed.success) throw badRequest({ watermark_position: ['Elegí una esquina válida.'] });
    kit.watermark_position = parsed.data;
  }
  const isDefault = form.get('is_default');
  if (typeof isDefault === 'string') kit.is_default = ['true', '1', 'on'].includes(isDefault.toLowerCase());
}

/** A single default per workspace, as the backend keeps it (BrandKitViewSet._save_one_default). */
function enforceSingleDefault(db: MockDb, kit: BrandKit): void {
  if (!kit.is_default) return;
  for (const other of db.brandKits) if (other.workspace === kit.workspace && other.id !== kit.id) other.is_default = false;
}

export const brandKitsHandlers = [
  route(contract.listBrandKits, ({ db, workspace }) => ({
    data: db.brandKits.filter((kit) => kit.workspace === workspace.id).sort((a, b) => Number(b.is_default) - Number(a.is_default) || a.created_at.localeCompare(b.created_at)),
  })),

  route(contract.createBrandKit, ({ db, workspace, user, form, now, require }) => {
    require('brandkit.manage');
    const kit: BrandKit = {
      id: newId(),
      workspace: workspace.id,
      user: user.id,
      name: '',
      description: '',
      primary_font: null,
      secondary_font: null,
      primary_color: '#FFFFFF',
      secondary_color: '#000000',
      accent_color: '#FFFF00',
      watermark_logo: null,
      intro_video: null,
      outro_video: null,
      is_default: false,
      created_at: new Date(now).toISOString(),
    };
    apply(kit, form ?? new FormData(), { partial: false });
    db.brandKits.push(kit);
    enforceSingleDefault(db, kit);
    return { data: kit };
  }),

  route(contract.getBrandKit, ({ db, workspace, params }) => ({ data: own(db, params.id, workspace.id) })),

  route(contract.updateBrandKit, ({ db, workspace, params, form, require }) => {
    require('brandkit.manage');
    const kit = own(db, params.id, workspace.id);
    apply(kit, form ?? new FormData(), { partial: true });
    enforceSingleDefault(db, kit);
    for (const project of db.projects) if (project.brand_kit === kit.id) project.brand_kit_name = kit.name;
    return { data: kit };
  }),

  route(contract.deleteBrandKit, ({ db, workspace, params, require }) => {
    require('brandkit.manage');
    const kit = own(db, params.id, workspace.id);
    db.brandKits = db.brandKits.filter((item) => item.id !== kit.id);
    // on_delete=SET_NULL
    for (const project of db.projects) {
      if (project.brand_kit !== kit.id) continue;
      project.brand_kit = null;
      delete project.brand_kit_name;
    }
    return { data: undefined };
  }),

  route(contract.duplicateBrandKit, ({ db, workspace, params, user, now, require }) => {
    require('brandkit.manage');
    const kit = own(db, params.id, workspace.id);
    // «Copia de X», then «Copia 2 de X»…: the first name the workspace does not have (brand_kit_service.copy_name).
    const taken = new Set(db.brandKits.filter((item) => item.workspace === workspace.id).map((item) => item.name));
    let number = 1;
    const copyName = () => `${number === 1 ? 'Copia de ' : `Copia ${number} de `}${kit.name}`.slice(0, 100);
    while (taken.has(copyName())) number += 1;
    const copy: BrandKit = { ...kit, id: newId(), user: user.id, name: copyName(), is_default: false, created_at: new Date(now).toISOString() };
    // Each file is copied to the folder of the new kit with its name, as the backend does: the two kits never share one.
    for (const field of FILE_FIELDS) {
      const url = kit[field];
      if (url) copy[field] = url.replace(/(\/[0-9a-f-]{36})?\/([^/]+)$/, `/${copy.id}/$2`);
    }
    db.brandKits.push(copy);
    return { data: copy };
  }),
];
