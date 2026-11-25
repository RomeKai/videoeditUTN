import type { ReactNode } from 'react';
import { Badge } from '@/ui/Badge';
import { Card } from '@/ui/Card';
import { Page } from '@/ui/Page';

export interface ScreenPlaceholderProps {
  /** Screen code of the map: "B2", "D1 · Transcripción". */
  code: string;
  /** Screen name, as the map calls it. */
  name: string;
  /** Artboard files in the design canvas (without the folder). */
  artboards: readonly string[];
  /** Domain that owns the screen (see ROUTES.md). */
  owner: string;
  /** What the screen is for, from the map. */
  purpose?: string;
  /** Route params, to show which record the route points at. */
  params?: Record<string, string | undefined>;
  /** False for screens that bring their own container (public cards, work screens). */
  page?: boolean;
  /** 2 when the route's layout already renders the page title (Uso, Ajustes, Perfil). */
  headingLevel?: 1 | 2;
  children?: ReactNode;
}

/**
 * Stand-in for a screen that is not built yet: says which screen of the map
 * goes here, which artboards draw it and which domain owns it. The screen's
 * owner replaces the page with the real thing; nothing else needs to change.
 */
export function ScreenPlaceholder({ code, name, artboards, owner, purpose, params, page = true, headingLevel = 1, children }: ScreenPlaceholderProps) {
  const body = (
    <Card padded data-screen={code} style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 720 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Badge tone="outline">{code}</Badge>
        <Badge tone="warn" dot>
          Pantalla pendiente
        </Badge>
      </div>
      {headingLevel === 1 ? <h1 className="t-h1">{name}</h1> : <h2 className="t-h2">{name}</h2>}
      {purpose ? <p className="muted">{purpose}</p> : null}
      <dl style={{ display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr)', gap: '6px 16px', margin: 0, fontSize: 13 }}>
        <dt className="subtle">Dominio</dt>
        <dd style={{ margin: 0 }}>{owner}</dd>
        <dt className="subtle">Diseño</dt>
        <dd style={{ margin: 0 }} className="code">
          {artboards.join(' · ')}
        </dd>
        {params
          ? Object.entries(params).map(([key, value]) => (
              <div key={key} style={{ display: 'contents' }}>
                <dt className="subtle">{key}</dt>
                <dd style={{ margin: 0 }} className="code">
                  {value ?? '—'}
                </dd>
              </div>
            ))
          : null}
      </dl>
      {children}
    </Card>
  );
  return page ? <Page>{body}</Page> : body;
}
