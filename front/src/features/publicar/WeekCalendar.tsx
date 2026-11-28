'use client';

import { useMemo, useState, type DragEvent } from 'react';
import type { ScheduledPost } from '@/api/contracts/social';
import { formatTime, formatTimeZone } from '@/lib/format';
import { cx } from '@/ui';
import { GroupNets, PostThumb, groupLabel } from './PostBits';
import styles from './Publicaciones.module.css';
import { WEEK_DOW, addDays, dayKey, groupKind, groupPosts, isDraggable, sameDay, shortDay, type GroupKind, type PostGroup } from './posts';

export const KIND_CLASS: Record<GroupKind, string | undefined> = {
  fail: styles.isFail,
  done: styles.isDone,
  sched: styles.isSched,
  send: undefined,
  draft: styles.isDraft,
};

const pad = (value: number) => String(value).padStart(2, '0');
const DEFAULT_HOURS: [number, number] = [8, 21];

export interface WeekCalendarProps {
  /** Monday of the week shown. */
  monday: Date;
  posts: ScheduledPost[];
  now: Date;
  selectedId?: string | null;
  onOpen?: (post: ScheduledPost) => void;
  /** Dropping a scheduled card on another cell. Absent: nothing can be dragged (role, or the empty state). */
  onMove?: (group: PostGroup, to: Date) => void;
  /** Decorative grid behind the empty state. */
  ghost?: boolean;
}

/** E2 · week: one column per day and one row per hour; each card is a clip at a time, with a state per network. */
export function WeekCalendar({ monday, posts, now, selectedId, onOpen, onMove, ghost = false }: WeekCalendarProps) {
  const days = useMemo(() => Array.from({ length: 7 }, (_, index) => addDays(monday, index)), [monday]);
  const groups = useMemo(() => groupPosts(posts), [posts]);
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [dropKey, setDropKey] = useState<string | null>(null);

  // 08–21 by default; the range grows to show whatever falls outside.
  const hours = useMemo(() => {
    let [first, last] = DEFAULT_HOURS;
    for (const group of groups) {
      first = Math.min(first, group.at.getHours());
      last = Math.max(last, group.at.getHours());
    }
    return Array.from({ length: last - first + 1 }, (_, index) => first + index);
  }, [groups]);

  const byCell = useMemo(() => {
    const map = new Map<string, PostGroup[]>();
    for (const group of groups) {
      const key = `${dayKey(group.at)}T${group.at.getHours()}`;
      map.set(key, [...(map.get(key) ?? []), group]);
    }
    return map;
  }, [groups]);

  const dragging = dragKey ? groups.find((group) => group.key === dragKey) : undefined;
  const target = (day: Date, hour: number): Date => {
    const date = new Date(day);
    date.setHours(hour, dragging ? dragging.at.getMinutes() : 0, 0, 0);
    return date;
  };
  const endDrag = () => {
    setDragKey(null);
    setDropKey(null);
  };

  const last = addDays(monday, 6);
  const label = `Publicaciones de la semana del ${shortDay(monday)} al ${shortDay(last)} de ${last.getFullYear()}`;

  return (
    <div className={styles.calScroll} aria-hidden={ghost || undefined}>
      <div className={styles.cal} role={ghost ? undefined : 'grid'} aria-label={ghost ? undefined : label}>
        <div className={cx(styles.calCorner, 'num')} role={ghost ? undefined : 'columnheader'}>
          {formatTimeZone(now)}
        </div>
        {days.map((day, index) => {
          const today = sameDay(day, now);
          return (
            <div key={dayKey(day)} className={cx(styles.calDay, today && styles.isToday)} role={ghost ? undefined : 'columnheader'} aria-current={today ? 'date' : undefined}>
              <span className={styles.dow}>{WEEK_DOW[index]}</span>
              <span className={cx(styles.dnum, 'num')}>{day.getDate()}</span>
            </div>
          );
        })}
        {hours.map((hour, row) => {
          const nowHere = now.getHours() === hour && days.some((day) => sameDay(day, now));
          return [
            <div key={`h${hour}`} className={cx(styles.calHour, 'num', row === 0 && styles.firstRow)} role={ghost ? undefined : 'rowheader'}>
              {pad(hour)}:00
              {nowHere ? (
                <span className={cx(styles.nowtag, 'num')} style={{ top: `${(now.getMinutes() / 60) * 100}%` }} title="Ahora">
                  {formatTime(now)}
                </span>
              ) : null}
            </div>,
            ...days.map((day) => {
              const key = `${dayKey(day)}T${hour}`;
              const cellGroups = ghost ? [] : (byCell.get(key) ?? []);
              const isNow = nowHere && sameDay(day, now);
              const droppable = Boolean(dragging && onMove && target(day, hour).getTime() > now.getTime());
              const onDragOver = (event: DragEvent) => {
                if (!droppable) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = 'move';
                if (dropKey !== key) setDropKey(key);
              };
              return (
                <div
                  key={key}
                  className={cx(styles.calCell, row === 0 && styles.firstRow, dropKey === key && styles.isDrop)}
                  role={ghost ? undefined : 'gridcell'}
                  onDragOver={onDragOver}
                  onDragLeave={() => {
                    if (dropKey === key) setDropKey(null);
                  }}
                  onDrop={(event) => {
                    if (!droppable || !dragging || !onMove) return;
                    event.preventDefault();
                    const to = target(day, hour);
                    endDrag();
                    if (to.getTime() !== dragging.at.getTime()) onMove(dragging, to);
                  }}
                >
                  {isNow ? <span className={styles.nowline} style={{ top: `${(now.getMinutes() / 60) * 100}%` }} title={`Ahora: ${formatTime(now)}`} /> : null}
                  {cellGroups.map((group) => {
                    const kind = groupKind(group);
                    const movable = Boolean(onMove) && isDraggable(group);
                    const first = group.posts[0] as ScheduledPost;
                    return (
                      <button
                        key={group.key}
                        type="button"
                        className={cx(
                          styles.pchip,
                          KIND_CLASS[kind],
                          kind === 'sched' && !movable && styles.notGrab,
                          dragKey === group.key && styles.isDragging,
                          group.posts.some((post) => post.id === selectedId) && styles.isSelected,
                        )}
                        aria-label={groupLabel(group)}
                        draggable={movable}
                        onDragStart={(event) => {
                          event.dataTransfer.effectAllowed = 'move';
                          event.dataTransfer.setData('text/plain', group.title);
                          setDragKey(group.key);
                        }}
                        onDragEnd={endDrag}
                        onClick={() => onOpen?.(first)}
                      >
                        <PostThumb clip={first.clip} xs className={styles.pthumb} />
                        <span className={styles.pbody}>
                          <span className={styles.prow}>
                            <span className={cx(styles.ptime, 'num')}>{formatTime(group.at)}</span>
                            <GroupNets posts={group.posts} />
                          </span>
                          <span className={styles.ptitle}>{group.title}</span>
                        </span>
                      </button>
                    );
                  })}
                  {dropKey === key && dragging ? <span className={cx(styles.dropLabel, 'num')}>{`${shortDay(day).slice(0, -4)} · ${formatTime(target(day, hour))}`}</span> : null}
                </div>
              );
            }),
          ];
        })}
      </div>
    </div>
  );
}
