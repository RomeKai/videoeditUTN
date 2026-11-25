'use client';

import { useMemo, useState } from 'react';
import type { ScheduledPost } from '@/api/contracts/social';
import { formatTime } from '@/lib/format';
import { Button, IconButton, Popover, cx } from '@/ui';
import { GroupNets, groupLabel } from './PostBits';
import styles from './Publicaciones.module.css';
import { KIND_CLASS } from './WeekCalendar';
import { WEEK_DOW, dayKey, groupKind, groupPosts, isDraggable, monthGrid, monthLabel, sameDay, shortDay, shortDayTitle, type PostGroup } from './posts';

/** Cards a day shows before "+N más". */
export const MONTH_DAY_LIMIT = 3;

export interface MonthCalendarProps {
  /** Any day of the month shown. */
  month: Date;
  posts: ScheduledPost[];
  now: Date;
  selectedId?: string | null;
  onOpen: (post: ScheduledPost) => void;
  /** Dropping a scheduled card on another day keeps its time. Absent: nothing can be dragged. */
  onMove?: (group: PostGroup, to: Date) => void;
  onOpenWeek: (day: Date) => void;
}

/** E2c · month: up to three cards per day, the rest behind "+N más". */
export function MonthCalendar({ month, posts, now, selectedId, onOpen, onMove, onOpenWeek }: MonthCalendarProps) {
  const days = useMemo(() => monthGrid(month), [month]);
  const groups = useMemo(() => groupPosts(posts), [posts]);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [dropKey, setDropKey] = useState<string | null>(null);
  const byDay = useMemo(() => {
    const map = new Map<string, PostGroup[]>();
    for (const group of groups) {
      const key = dayKey(group.at);
      map.set(key, [...(map.get(key) ?? []), group]);
    }
    return map;
  }, [groups]);
  const dragging = dragKey ? groups.find((group) => group.key === dragKey) : undefined;
  const target = (day: Date): Date => {
    const date = new Date(day);
    if (dragging) date.setHours(dragging.at.getHours(), dragging.at.getMinutes(), 0, 0);
    return date;
  };
  const endDrag = () => {
    setDragKey(null);
    setDropKey(null);
  };

  const chip = (group: PostGroup, after?: () => void) => {
    const kind = groupKind(group);
    const movable = Boolean(onMove) && isDraggable(group);
    const first = group.posts[0] as ScheduledPost;
    return (
      <button
        key={group.key}
        type="button"
        className={cx(
          styles.mchip,
          KIND_CLASS[kind],
          dragKey === group.key && styles.isDragging,
          group.posts.some((post) => post.id === selectedId) && styles.isSelected,
        )}
        aria-label={groupLabel(group)}
        title={group.title}
        draggable={movable}
        onDragStart={(event) => {
          event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain', group.title);
          setDragKey(group.key);
        }}
        onDragEnd={endDrag}
        onClick={() => {
          after?.();
          onOpen(first);
        }}
      >
        <span className={cx(styles.mtime, 'num')}>{formatTime(group.at)}</span>
        <span className={styles.mtitle}>{group.title}</span>
        <GroupNets posts={group.posts} plain />
      </button>
    );
  };

  return (
    <div className={styles.calScroll}>
      <div className={styles.mcal} role="grid" aria-label={`Publicaciones de ${monthLabel(month).toLowerCase().replace(' ', ' de ')}`}>
        {WEEK_DOW.map((dow) => (
          <div key={dow} className={styles.mdow} role="columnheader">
            {dow}
          </div>
        ))}
        {days.map((day) => {
          const key = dayKey(day);
          const dayGroups = byDay.get(key) ?? [];
          const today = sameDay(day, now);
          const out = day.getMonth() !== month.getMonth();
          const rest = dayGroups.length - MONTH_DAY_LIMIT;
          const droppable = Boolean(dragging && onMove && target(day).getTime() > now.getTime());
          return (
            <div
              key={key}
              className={cx(styles.mcell, out && styles.isOut, today && styles.isToday, dropKey === key && styles.isDrop)}
              role="gridcell"
              aria-current={today ? 'date' : undefined}
              onDragOver={(event) => {
                if (!droppable) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = 'move';
                if (dropKey !== key) setDropKey(key);
              }}
              onDragLeave={() => {
                if (dropKey === key) setDropKey(null);
              }}
              onDrop={(event) => {
                if (!droppable || !dragging || !onMove) return;
                event.preventDefault();
                const to = target(day);
                endDrag();
                if (to.getTime() !== dragging.at.getTime()) onMove(dragging, to);
              }}
            >
              <span className={styles.mnumRow}>
                <span className={cx(styles.mnum, 'num')}>{day.getDate() === 1 ? shortDay(day).slice(4) : day.getDate()}</span>
                {today ? <span className={cx('t-label', styles.mhoy)}>Hoy</span> : null}
              </span>
              {dayGroups.slice(0, MONTH_DAY_LIMIT).map((group) => chip(group))}
              {rest > 0 ? (
                <Popover
                  ariaLabel={`Publicaciones del ${shortDay(day)}`}
                  panelClassName={styles.mpop}
                  trigger={(props) => (
                    <button type="button" className={styles.mmore} {...props}>
                      +{rest} más
                    </button>
                  )}
                >
                  {({ close }) => (
                    <>
                      <div className={styles.mpopHead}>
                        <span>{shortDayTitle(day)}</span>
                        <span className="t-label subtle num">{dayGroups.length} publicaciones</span>
                        <span className={styles.spacer} />
                        <IconButton icon="close" label="Cerrar" size="sm" onClick={close} />
                      </div>
                      {dayGroups.map((group) => chip(group, close))}
                      <Button
                        variant="ghost"
                        size="sm"
                        iconRight="chevron-right"
                        className={styles.mpopWeek}
                        onClick={() => {
                          close();
                          onOpenWeek(day);
                        }}
                      >
                        Ver la semana
                      </Button>
                    </>
                  )}
                </Popover>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
