import { useState } from 'react';

interface StreamEventLike {
  type: string;
  message?: string;
  agent?: string;
  files?: string[];
  [k: string]: unknown;
}

interface ReasoningPanelProps {
  events: StreamEventLike[];
}

export default function ReasoningPanel({ events }: ReasoningPanelProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div style={{ borderBottom: '1px solid #333' }}>
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        style={{
          width: '100%',
          padding: '8px 12px',
          textAlign: 'left',
          background: '#2a2a2a',
          border: 'none',
          color: '#d4d4d4',
          cursor: 'pointer',
        }}
      >
        {collapsed ? '▶' : '▼'} Reasoning trace
      </button>
      {!collapsed && (
        <div style={{ maxHeight: 180, overflow: 'auto', padding: 8, fontSize: 12 }}>
          {events.length === 0 && <span style={{ color: '#666' }}>No events yet.</span>}
          {events.map((e, i) => (
            <div key={i} style={{ marginBottom: 4 }}>
              {e.type === 'status' && <span style={{ color: '#888' }}>{e.message}</span>}
              {e.type === 'agent_step' && (
                <span><strong>{e.agent}</strong>: {e.message}</span>
              )}
              {e.type === 'retrieval' && (
                <span>Retrieved: {(e.files ?? []).join(', ') || '—'}</span>
              )}
              {e.type === 'diff' && <span>Diff produced ({String((e.diff as string)?.length ?? 0)} chars)</span>}
              {e.type === 'final' && <span>Done.</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
