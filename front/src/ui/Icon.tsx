import { ICON_PATHS, type IconName } from './icon-paths';

export type { IconName };

export interface IconProps {
  name: IconName;
  /** 14, 16 or 20 px in the design; 16 by default. */
  size?: number;
  className?: string;
  /** Accessible name. Without it the icon is decorative. */
  label?: string;
}

export function Icon({ name, size = 16, className, label }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={size <= 16 ? 2 : 1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ flex: 'none' }}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      // Static, hand-checked path data (see icon-paths.ts).
      dangerouslySetInnerHTML={{ __html: ICON_PATHS[name] }}
    />
  );
}
