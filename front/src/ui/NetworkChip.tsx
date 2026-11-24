import { cx } from '@/lib/cx';
import { Icon, type IconName } from './Icon';
import styles from './NetworkChip.module.css';

/** Values of ScheduledPost.Platform in the backend. */
export type NetworkPlatform = 'TIKTOK' | 'INSTAGRAM_REELS' | 'YOUTUBE_SHORTS';

export const NETWORKS: Record<NetworkPlatform, { name: string; shortName: string; icon: IconName }> = {
  TIKTOK: { name: 'TikTok', shortName: 'TikTok', icon: 'net-tiktok' },
  INSTAGRAM_REELS: { name: 'Instagram Reels', shortName: 'Instagram', icon: 'net-instagram' },
  YOUTUBE_SHORTS: { name: 'YouTube Shorts', shortName: 'YouTube', icon: 'net-youtube' },
};

export interface NetworkChipProps {
  platform: NetworkPlatform;
  size?: 'sm' | 'md';
  /**
   * The network name always goes next to the mark, in text. Only calendar chips
   * hide it (then the mark gets the name as its accessible label).
   */
  hideName?: boolean;
  short?: boolean;
  className?: string;
}

/** Generic line glyph in a tile (kit: .net). Never a brand logo. */
export function NetworkChip({ platform, size = 'md', hideName = false, short = false, className }: NetworkChipProps) {
  const network = NETWORKS[platform];
  const mark = (
    <span
      className={cx(styles.net, size === 'sm' && styles.sm, hideName && className)}
      role={hideName ? 'img' : undefined}
      aria-label={hideName ? network.name : undefined}
      aria-hidden={hideName ? undefined : true}
    >
      <Icon name={network.icon} size={size === 'sm' ? 11 : 14} />
    </span>
  );
  if (hideName) return mark;
  return (
    <span className={cx(styles.chip, size === 'sm' && styles.chipSm, className)}>
      {mark}
      <span>{short ? network.shortName : network.name}</span>
    </span>
  );
}
