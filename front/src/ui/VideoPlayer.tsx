'use client';

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { cx } from '@/lib/cx';
import { formatTimecode } from '@/lib/format';
import { IconButton } from './Button';
import { Icon } from './Icon';
import { Slider } from './Slider';
import { VideoThumb, type AspectRatio } from './VideoThumb';
import styles from './VideoPlayer.module.css';

export interface VideoPlayerHandle {
  play: () => void;
  pause: () => void;
  toggle: () => void;
  /** Jumps to a time in seconds (clamped to the playable range). */
  seek: (seconds: number) => void;
  getCurrentTime: () => number;
}

export interface VideoPlayerProps {
  /** Video URL (presigned proxy or clip). Without it the placeholder frame is shown and time is simulated. */
  src?: string | null;
  /** Seed of the placeholder hue (project or clip id). */
  seed?: string;
  ratio?: AspectRatio;
  /** Total duration in seconds, used until the media reports its own (and always for the placeholder). */
  duration?: number;
  /** Plays only this range, e.g. the selected clip inside the source video. */
  range?: { start: number; end: number };
  captionColor?: string;
  /** Accessible name: "Reproductor: Atardecer en Potrerillos". */
  label: string;
  onTimeUpdate?: (seconds: number) => void;
  /** Keyboard `I` and `O`: mark in and out at the current time (review and editor). */
  onMarkIn?: (seconds: number) => void;
  onMarkOut?: (seconds: number) => void;
  /** Extra keys (A, X, ↑, ↓ in the review). Return true when handled. */
  onShortcut?: (key: string) => boolean;
  /** Drawn over the picture: crop guides, live subtitle sample… */
  overlay?: ReactNode;
  /** Under the controls: timeline with clip marks, waveform… */
  children?: ReactNode;
  className?: string;
}

/** Mock media: there is no real file behind it. */
export function isPlaceholderSource(url: string | null | undefined): boolean {
  return Boolean(url && url.startsWith('mock:'));
}

function sameMedia(a: string | null, b: string | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  const strip = (url: string) => url.split(/[?#]/)[0];
  return strip(a) === strip(b);
}

const RATIO_CLASS: Record<AspectRatio, string> = {
  '16:9': styles.r16x9 ?? '',
  '9:16': styles.r9x16 ?? '',
  '1:1': styles.r1x1 ?? '',
};

/**
 * Player wrapper with the product's shortcuts (map 1.10): space play/pause,
 * ← → ±5 s (Shift ±1 s), I mark in, O mark out. Works without a source:
 * mock mode has no real media, so it shows the design's placeholder frame and
 * advances a simulated clock, which keeps timelines and transcripts in sync.
 */
export const VideoPlayer = forwardRef<VideoPlayerHandle, VideoPlayerProps>(function VideoPlayer(
  {
    src: srcProp,
    seed,
    ratio = '16:9',
    duration = 0,
    range,
    captionColor,
    label,
    onTimeUpdate,
    onMarkIn,
    onMarkOut,
    onShortcut,
    overlay,
    children,
    className,
  },
  ref,
) {
  const videoRef = useRef<HTMLVideoElement>(null);
  // Presigned URLs change their query string on every poll while pointing at the same file:
  // only a different origin or path is a new source, otherwise playback would restart.
  const [stableSrc, setStableSrc] = useState(srcProp ?? null);
  useEffect(() => {
    setStableSrc((current) => (sameMedia(current, srcProp ?? null) ? current : (srcProp ?? null)));
  }, [srcProp]);
  // A source that cannot be played (the mock's `mock:` URLs, a link that expired, a codec the browser
  // lacks) falls back to the placeholder frame and the simulated clock instead of a broken video.
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const src = stableSrc && !isPlaceholderSource(stableSrc) && failedSrc !== stableSrc ? stableSrc : null;
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(range?.start ?? 0);
  const [mediaDuration, setMediaDuration] = useState<number | null>(null);
  const timeRef = useRef(time);
  timeRef.current = time;
  const onTimeUpdateRef = useRef(onTimeUpdate);
  onTimeUpdateRef.current = onTimeUpdate;

  const total = mediaDuration ?? duration;
  const min = range?.start ?? 0;
  const max = range?.end ?? total;

  const applyTime = useCallback((seconds: number) => {
    setTime(seconds);
    onTimeUpdateRef.current?.(seconds);
  }, []);

  const seek = useCallback(
    (seconds: number) => {
      const next = Math.min(Math.max(seconds, min), Math.max(min, max));
      if (videoRef.current && src) videoRef.current.currentTime = next;
      applyTime(next);
    },
    [applyTime, max, min, src],
  );

  const play = useCallback(() => {
    if (timeRef.current >= max && max > min) seek(min);
    if (videoRef.current && src) void videoRef.current.play().catch(() => setPlaying(false));
    setPlaying(true);
  }, [max, min, seek, src]);

  const pause = useCallback(() => {
    if (videoRef.current && src) videoRef.current.pause();
    setPlaying(false);
  }, [src]);

  const toggle = useCallback(() => (playing ? pause() : play()), [pause, play, playing]);

  useImperativeHandle(ref, () => ({ play, pause, toggle, seek, getCurrentTime: () => timeRef.current }), [
    pause,
    play,
    seek,
    toggle,
  ]);

  // Simulated clock when there is no media to play.
  useEffect(() => {
    if (src || !playing) return;
    const timer = window.setInterval(() => {
      const next = timeRef.current + 0.25;
      if (next >= max) {
        applyTime(max);
        setPlaying(false);
      } else {
        applyTime(next);
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, [applyTime, max, playing, src]);

  // Jump to the start of a new range (another clip was selected).
  useEffect(() => {
    if (range) seek(range.start);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range?.start, range?.end]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.tagName === 'INPUT' && (target as HTMLInputElement).type !== 'range') return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const key = event.key;
    if (key === ' ' || key === 'k') {
      if (target.tagName === 'BUTTON') return;
      event.preventDefault();
      toggle();
    } else if (key === 'ArrowLeft' || key === 'ArrowRight') {
      event.preventDefault();
      const step = event.shiftKey ? 1 : 5;
      seek(timeRef.current + (key === 'ArrowLeft' ? -step : step));
    } else if ((key === 'i' || key === 'I') && onMarkIn) {
      event.preventDefault();
      onMarkIn(timeRef.current);
    } else if ((key === 'o' || key === 'O') && onMarkOut) {
      event.preventDefault();
      onMarkOut(timeRef.current);
    } else if (onShortcut?.(key)) {
      event.preventDefault();
    }
  };

  return (
    <div className={cx(styles.player, className)} role="group" aria-label={label} onKeyDown={onKeyDown}>
      <div className={cx(styles.stage, RATIO_CLASS[ratio])}>
        {src ? (
          <video
            ref={videoRef}
            className={styles.video}
            src={src}
            playsInline
            preload="metadata"
            onLoadedMetadata={(event) => {
              const value = event.currentTarget.duration;
              if (Number.isFinite(value)) setMediaDuration(value);
              if (range) event.currentTarget.currentTime = range.start;
            }}
            onTimeUpdate={(event) => {
              const current = event.currentTarget.currentTime;
              if (range && current >= range.end) {
                event.currentTarget.pause();
                setPlaying(false);
                applyTime(range.end);
                return;
              }
              applyTime(current);
            }}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onError={() => {
              setPlaying(false);
              setFailedSrc(src);
            }}
          />
        ) : (
          <div className={styles.placeholder}>
            <VideoThumb ratio={ratio} seed={seed} captions={ratio === '9:16' ? (captionColor ?? true) : false} />
          </div>
        )}
        {overlay ? <div className={styles.overlay}>{overlay}</div> : null}
        {!playing ? (
          <button type="button" className={styles.bigPlay} aria-label="Reproducir" onClick={play} tabIndex={-1}>
            <span>
              <Icon name="play" size={20} />
            </span>
          </button>
        ) : null}
      </div>
      <div className={styles.controls}>
        <IconButton icon={playing ? 'pause' : 'play'} label={playing ? 'Pausar' : 'Reproducir'} onClick={toggle} />
        <Slider
          className={styles.seek}
          aria-label="Posición"
          min={min}
          max={Math.max(min + 0.1, max)}
          step={0.1}
          value={Math.min(Math.max(time, min), Math.max(min, max))}
          aria-valuetext={formatTimecode(time)}
          onChange={seek}
        />
        <span className={cx('timecode', styles.time)}>
          {formatTimecode(time)} / {formatTimecode(max)}
        </span>
      </div>
      {children}
    </div>
  );
});
