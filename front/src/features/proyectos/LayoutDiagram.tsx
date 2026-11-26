import type { CSSProperties } from 'react';
import type { AspectRatio, Layout } from '@/api/contracts/projects';
import { cx } from '@/ui';
import styles from './NewProject.module.css';

const FRAME: Record<AspectRatio, { width: number; ratio: string }> = {
  '9:16': { width: 36, ratio: '9 / 16' },
  '1:1': { width: 44, ratio: '1 / 1' },
  '16:9': { width: 48, ratio: '16 / 9' },
};

/** The 2.35:1 band of the cinematic layout in each output format: [top %, height %]. */
const CINEMATIC_BAND: Record<AspectRatio, [number, number]> = { '9:16': [38, 24], '1:1': [29, 42], '16:9': [12, 76] };

/** Where a horizontal source lands when it is fitted inside each output format: [top %, height %]. */
const FIT_BAND: Record<AspectRatio, [number, number]> = { '9:16': [36, 28], '1:1': [22, 56], '16:9': [4, 92] };

const box = (style: CSSProperties, className: string | undefined) => <span className={className} style={style} />;

/**
 * Schematic drawing of a layout inside the chosen output format (map C1).
 * Versus in 1:1 and 16:9 keeps each half tall and leaves black at the sides of
 * the panel, and the drawing shows it that way.
 */
export function LayoutDiagram({ layout, ratio }: { layout: Layout; ratio: AspectRatio }) {
  const frame = FRAME[ratio];
  const [top, height] = FIT_BAND[ratio];
  const wide = ratio !== '9:16';
  let shapes;
  switch (layout) {
    case 'fill':
      shapes = box({ inset: 2 }, styles.dgV);
      break;
    case 'fit':
      shapes = box({ left: 2, right: 2, top: `${top}%`, height: `${height}%` }, styles.dgV);
      break;
    case 'blurred':
      shapes = (
        <>
          {box({ inset: 0 }, styles.dgBlur)}
          {box({ left: 0, right: 0, top: `${top}%`, height: `${height}%` }, styles.dgV)}
        </>
      );
      break;
    case 'split':
      shapes = (
        <>
          {box({ left: 2, right: 2, top: 2, height: '46%' }, styles.dgV)}
          {box({ left: 2, right: 2, bottom: 2, height: '46%' }, styles.dgV2)}
        </>
      );
      break;
    case 'pip':
      shapes = (
        <>
          {box({ inset: 2 }, styles.dgV2)}
          {box({ right: 4, bottom: 4, width: '42%', height: wide ? '40%' : '24%' }, cx(styles.dgV, styles.dgInset))}
        </>
      );
      break;
    case 'versus':
      shapes = wide ? (
        <>
          {box({ inset: 0 }, styles.dgBlack)}
          {box({ left: '9%', top: 2, bottom: 2, width: '32%' }, styles.dgV)}
          {box({ right: '9%', top: 2, bottom: 2, width: '32%' }, styles.dgV2)}
        </>
      ) : (
        <>
          {box({ left: 2, top: 2, bottom: 2, width: '44%' }, styles.dgV)}
          {box({ right: 2, top: 2, bottom: 2, width: '44%' }, styles.dgV2)}
        </>
      );
      break;
    case 'active':
      shapes = (
        <>
          {box({ inset: 2 }, styles.dgV2)}
          {box({ left: '22%', top: '24%', width: '56%', height: '36%' }, styles.dgRing)}
          {box({ left: '38%', top: '33%', width: '24%', height: '14%', borderRadius: '50%' }, styles.dgV)}
        </>
      );
      break;
    case 'reaction':
      shapes = (
        <>
          {box({ left: 2, right: 2, top: 2, height: '46%' }, styles.dgV2)}
          {box({ left: 2, right: 2, bottom: 2, height: '46%' }, styles.dgV)}
        </>
      );
      break;
    case 'cinematic': {
      const [bandTop, bandHeight] = CINEMATIC_BAND[ratio];
      shapes = (
        <>
          {box({ inset: 0 }, styles.dgBlack)}
          {box({ left: 0, right: 0, top: `${bandTop}%`, height: `${bandHeight}%` }, styles.dgV)}
        </>
      );
      break;
    }
  }
  return (
    <span className={styles.dgBox} aria-hidden="true">
      <span className={styles.dg} style={{ width: frame.width, aspectRatio: frame.ratio }} data-layout={layout} data-ratio={ratio}>
        {shapes}
      </span>
    </span>
  );
}
