import { useRef, useState } from 'react';
import type { StreamEvent } from '../api/agent';
import { runAgentStream } from '../api/agent';
import { logger } from '../lib/logger';

interface ChatPanelProps {
  sessionId: string | null;
  onReferences: (refs: { file: string; line: number }[]) => void;
  onDiff: (diff: string) => void;
  onStreamEvent?: (event: StreamEvent) => void;
  onMessageSend?: () => void;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  references?: { file: string; line: number }[];
}

function WebIcon({ on }: { on: boolean }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill={on ? 'currentColor' : 'none'}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ color: on ? '#58a6ff' : '#888' }}
      aria-hidden
    >
      <circle cx="12" cy="12" r="10" />
      <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}

export default function ChatPanel({ sessionId, onReferences, onDiff, onStreamEvent, onMessageSend }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [searchMode, setSearchMode] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);

  function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!sessionId || !input.trim() || streaming) return;
    const userMsg = input.trim();
    logger.info('ChatPanel', 'send message', { sessionId, messageLen: userMsg.length });
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: userMsg }]);
    onMessageSend?.();
    setStreaming(true);
    let assistantContent = '';
    const assistantId = messages.length + 1;

    setMessages((prev) => [...prev, { role: 'assistant', content: '' }]);

    abortRef.current = runAgentStream(sessionId, userMsg, (event: StreamEvent) => {
      onStreamEvent?.(event);
      if (event.type === 'final') {
        logger.debug('ChatPanel', 'final event', { refs: event.references?.length });
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === 'assistant')
            next[next.length - 1] = { ...last, content: event.message ?? last.content, references: event.references };
          return next;
        });
        if (event.references?.length) onReferences(event.references);
        setStreaming(false);
      } else if (event.type === 'agent_step' && event.message) {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === 'assistant')
            next[next.length - 1] = { ...last, content: event.message ?? '' };
          return next;
        });
      } else if (event.type === 'diff' && event.diff) {
        logger.info('ChatPanel', 'diff received', { diffLen: event.diff.length });
        onDiff(event.diff);
      }
    }, searchMode);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
        {messages.length === 0 && (
          <div style={{ color: '#888' }}>Ask about the codebase, request changes, or paste an error.</div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              marginBottom: 12,
              padding: 8,
              background: m.role === 'user' ? '#2a2a2a' : 'transparent',
              borderRadius: 6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            <strong>{m.role === 'user' ? 'You' : 'Assistant'}</strong>
            <div>{m.content || '…'}</div>
            {m.references?.length ? (
              <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
                Refs: {m.references.map((r) => `${r.file}:${r.line}`).join(', ')}
              </div>
            ) : null}
          </div>
        ))}
      </div>
      <form onSubmit={handleSend} style={{ padding: 8, borderTop: '1px solid #333' }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend(e)}
          placeholder="Message…"
          disabled={!sessionId || streaming}
          rows={2}
          style={{
            width: '100%',
            padding: 8,
            borderRadius: 6,
            border: '1px solid #444',
            background: '#1e1e1e',
            color: '#d4d4d4',
            resize: 'vertical',
          }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
          <button
            type="button"
            onClick={() => setSearchMode((s) => !s)}
            title={searchMode ? 'Search the web (on)' : 'Search the web (off)'}
            style={{
              padding: 6,
              border: '1px solid #444',
              borderRadius: 6,
              background: searchMode ? '#1a3a5c' : '#1e1e1e',
              cursor: 'pointer',
            }}
            aria-pressed={searchMode}
          >
            <WebIcon on={searchMode} />
          </button>
          <button type="submit" disabled={!sessionId || streaming || !input.trim()}>
            Send
          </button>
        </div>
      </form>
    </div>
  );
}
