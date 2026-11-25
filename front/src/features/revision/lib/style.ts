import type { Project } from '@/api/contracts/projects';
import { formatNumber } from '@/lib/format';
import { LABELS } from '@/lib/project-status';

type StyleSource = Pick<
  Project,
  | 'aspect_ratio'
  | 'editing_style'
  | 'render_layout'
  | 'gameplay_position'
  | 'camera_selection_position'
  | 'manual_camera_x'
  | 'manual_camera_y'
  | 'manual_camera_zoom'
  | 'use_facetracking'
  | 'add_subtitles'
  | 'subtitle_color'
  | 'subtitle_size'
  | 'subtitle_position'
  | 'subtitle_words_per_segment'
  | 'remove_silences'
  | 'brand_kit_name'
  | 'metadata'
>;

export interface StyleRow {
  key: string;
  label: string;
  value: string;
  /** Hex color to draw next to the value (subtitles). */
  swatch?: string;
}

/** The manual camera is off while it keeps its default values (map C1 › Opciones avanzadas). */
export function isManualCamera(project: Pick<Project, 'manual_camera_x' | 'manual_camera_y' | 'manual_camera_zoom'>): boolean {
  return !(project.manual_camera_x === 0.5 && project.manual_camera_y === 0.2 && project.manual_camera_zoom === 1);
}

/**
 * The project's style as the editor shows it (read only: the style is changed
 * in the project and applies to every clip). The advanced options follow the
 * map: gameplay position and camera side only for Dividido, the manual camera
 * for Dividido and Versus, where it replaces the side and "Seguir caras".
 */
export function describeStyle(project: StyleSource): StyleRow[] {
  const layout = project.render_layout;
  const manual = (layout === 'split' || layout === 'versus') && isManualCamera(project);
  const rows: StyleRow[] = [
    { key: 'format', label: 'Formato', value: `${project.aspect_ratio} · ${project.metadata.render_resolution ?? '720p'}` },
    { key: 'style', label: 'Estilo', value: LABELS.editing_style[project.editing_style] },
    { key: 'layout', label: 'Layout', value: LABELS.render_layout[layout] },
  ];
  if (layout === 'split') {
    rows.push({ key: 'gameplay', label: 'Posición del gameplay', value: LABELS.position[project.gameplay_position] });
  }
  if (layout === 'split' || layout === 'versus') {
    const side = project.camera_selection_position === 'center' ? 'Todo el cuadro' : LABELS.position[project.camera_selection_position];
    rows.push({
      key: 'camera',
      label: 'Cámara',
      value: manual
        ? `Manual · x ${formatNumber(project.manual_camera_x)} · y ${formatNumber(project.manual_camera_y)} · zoom ${formatNumber(project.manual_camera_zoom)}`
        : layout === 'split'
          ? `Lado: ${side}`
          : 'Automática',
    });
  }
  rows.push({
    key: 'faces',
    label: 'Seguir caras',
    value: manual ? 'No aplica: la cámara manual lo reemplaza' : project.use_facetracking ? 'Sí' : 'No',
  });
  rows.push(
    project.add_subtitles
      ? {
          key: 'subtitles',
          label: 'Subtítulos',
          value: `${LABELS.subtitle_size[project.subtitle_size]} · ${LABELS.subtitle_position[project.subtitle_position]} · ${project.subtitle_words_per_segment} ${project.subtitle_words_per_segment === 1 ? 'palabra' : 'palabras'}`,
          swatch: project.subtitle_color,
        }
      : { key: 'subtitles', label: 'Subtítulos', value: 'Sin subtítulos' },
  );
  rows.push({ key: 'silences', label: 'Quitar silencios', value: project.remove_silences ? 'Sí' : 'No' });
  if (project.brand_kit_name) rows.push({ key: 'brand', label: 'Brand kit', value: project.brand_kit_name });
  return rows;
}
