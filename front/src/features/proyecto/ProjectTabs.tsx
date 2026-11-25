'use client';

import Link from 'next/link';
import { Tooltip } from '@/ui';
import type { ProjectTab, ProjectTabKey } from './model';
import styles from './ProjectShell.module.css';

/**
 * Sub-tabs of a project. Like <NavTabs>, plus what the project needs: a tab
 * that is not available yet stays visible, disabled, and says why; Clips shows
 * its count. On phones the row becomes a full-width selector (artboard M2).
 */
export function ProjectTabs({ tabs, current }: { tabs: ProjectTab[]; current: ProjectTabKey }) {
  return (
    <nav className={styles.tabs} aria-label="Secciones del proyecto">
      {tabs.map((tab) => {
        const content = (
          <>
            {tab.label}
            {tab.count !== undefined ? <span className={styles.tabCount}>{tab.count}</span> : null}
          </>
        );
        if (tab.disabledReason && tab.key !== current) {
          return (
            <Tooltip key={tab.key} content={tab.disabledReason}>
              <span className={styles.tab} role="link" aria-disabled="true" tabIndex={0}>
                {content}
              </span>
            </Tooltip>
          );
        }
        return (
          <Link key={tab.key} href={tab.href} className={styles.tab} aria-current={tab.key === current ? 'page' : undefined}>
            {content}
          </Link>
        );
      })}
    </nav>
  );
}
