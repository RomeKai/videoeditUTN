'use client';

/**
 * Last resort: the root layout itself failed, so there are no providers,
 * fonts or tokens. Plain markup with the same message as K3.
 */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="es-AR">
      <body style={{ margin: 0, minHeight: '100vh', display: 'grid', placeItems: 'center', background: '#0a0a0b', color: '#ededf0', fontFamily: 'system-ui, sans-serif' }}>
        <main style={{ textAlign: 'center', padding: 24 }}>
          <h1 style={{ fontSize: 20, margin: '0 0 8px' }}>Algo falló de nuestro lado</h1>
          <p style={{ color: '#a1a1aa', margin: '0 0 24px' }}>Probá de nuevo en un momento.</p>
          <button
            type="button"
            onClick={reset}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, border: 0, background: '#ededf0', color: '#0a0a0b', fontWeight: 500, cursor: 'pointer' }}
          >
            Reintentar
          </button>
        </main>
      </body>
    </html>
  );
}
