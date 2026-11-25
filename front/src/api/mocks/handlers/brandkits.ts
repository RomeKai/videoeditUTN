import type { BrandKit } from '../../contracts/brandkits';
import * as contract from '../../contracts/brandkits';
import { newId, type MockDb } from '../db';
import { badRequest, notFound, route } from '../http';

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

/** The mock keeps a single default per workspace, as the map asks (the backend does not guarantee it yet). */
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
    // The first kit of a workspace becomes its default.
    if (!db.brandKits.some((item) => item.workspace === workspace.id)) kit.is_default = true;
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
    const copy: BrandKit = { ...kit, id: newId(), user: user.id, name: `Copia de ${kit.name}`.slice(0, 100), is_default: false, created_at: new Date(now).toISOString() };
    db.brandKits.push(copy);
    return { data: copy };
  }),
];
