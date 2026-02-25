import { useState } from 'react';
import { applyPatch } from '../api/agent';
import { logger } from '../lib/logger';

interface DiffViewerProps {
  diff: string;
  sessionId: string | null;
  onClose: () => void;
  onApplied: (updatedFiles?: Record<string, string>) => void;
}

export default function DiffViewer({ diff, sessionId, onClose, onApplied }: DiffViewerProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAccept() {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    logger.info('Diff', 'apply patch', { sessionId, diffLen: diff.length });
    try {
      const res = await applyPatch(sessionId, diff);
      if (res.success) {
        logger.info('Diff', 'patch applied', { sessionId, files: res.updated_files && Object.keys(res.updated_files) });
        onApplied(res.updated_files);
        onClose();
      } else {
        logger.warn('Diff', 'patch apply failed', { sessionId, error: res.error ?? res.message });
        setError(res.error ?? res.message);
      }
    } catch (e) {
      logger.error('Diff', 'patch apply error', { sessionId, error: (e as Error).message });
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.7)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        style={{
          background: '#1e1e1e',
          border: '1px solid #444',
          borderRadius: 8,
          maxWidth: '90%',
          maxHeight: '80%',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <div style={{ padding: 12, borderBottom: '1px solid #333', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <strong>Diff preview</strong>
          <button type="button" onClick={onClose}>Close</button>
        </div>
        <pre
          style={{
            flex: 1,
            overflow: 'auto',
            padding: 16,
            margin: 0,
            fontSize: 12,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {diff}
        </pre>
        {error && <div style={{ padding: 8, color: '#f88' }}>{error}</div>}
        <div style={{ padding: 12, borderTop: '1px solid #333', display: 'flex', gap: 8 }}>
          <button type="button" onClick={handleAccept} disabled={!sessionId || loading}>
            {loading ? 'Applying…' : 'Accept'}
          </button>
          <button type="button" onClick={onClose}>Reject</button>
        </div>
      </div>
    </div>
  );
}
