import { useState } from 'react';
import type { TreeEntry } from './api/agent';
import { logger } from './lib/logger';
import RepoLoader from './components/RepoLoader';
import FileTree from './components/FileTree';
import CodeEditor from './components/CodeEditor';
import ChatPanel from './components/ChatPanel';
import DiffViewer from './components/DiffViewer';
import ReasoningPanel from './components/ReasoningPanel';
import AgentStatusBar from './components/AgentStatusBar';

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeEntry[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [references, setReferences] = useState<{ file: string; line: number }[]>([]);
  const [pendingDiff, setPendingDiff] = useState<string | null>(null);
  const [streamEvents, setStreamEvents] = useState<Array<{ type: string; [k: string]: unknown }>>([]);
  const [currentAgent, setCurrentAgent] = useState<string | null>(null);
  const [editorRefreshKey, setEditorRefreshKey] = useState(0);

  function handleRepoLoaded(sid: string, t: TreeEntry[]) {
    logger.info('App', 'handleRepoLoaded', { sessionId: sid, treeEntries: t.length });
    setSessionId(sid);
    setTree(t);
    setSelectedPath(null);
    setReferences([]);
    setPendingDiff(null);
    setStreamEvents([]);
    setCurrentAgent(null);
    setEditorRefreshKey(0);
  }

  function handlePatchApplied(_updatedFiles?: Record<string, string>) {
    setPendingDiff(null);
    setEditorRefreshKey((k) => k + 1);
  }

  function handleStreamEvent(event: { type: string; agent?: string; references?: { file: string; line: number }[]; [k: string]: unknown }) {
    logger.debug('App', 'stream event', { type: event.type, agent: event.agent });
    setStreamEvents((prev) => [...prev, event]);
    if (event.type === 'agent_step' && event.agent) setCurrentAgent(event.agent);
    if (event.type === 'final' && event.references?.length) setSelectedPath((p) => event.references![0].file ?? p);
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <header style={{ padding: '8px 16px', borderBottom: '1px solid #333', display: 'flex', alignItems: 'center', gap: 16 }}>
        <span style={{ fontWeight: 600 }}>Cursor-like AI Assistant</span>
        <RepoLoader onLoaded={handleRepoLoaded} />
      </header>
      <AgentStatusBar agent={currentAgent} />
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <aside style={{ width: 260, borderRight: '1px solid #333', overflow: 'auto' }}>
          <FileTree sessionId={sessionId} onSelectFile={setSelectedPath} selectedPath={selectedPath} />
        </aside>
        <main style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <CodeEditor sessionId={sessionId} filePath={selectedPath} references={references} refreshKey={editorRefreshKey} />
        </main>
        <aside style={{ width: 380, borderLeft: '1px solid #333', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <ReasoningPanel events={streamEvents} />
          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            <ChatPanel
              sessionId={sessionId}
              onReferences={setReferences}
              onDiff={setPendingDiff}
              onStreamEvent={handleStreamEvent}
              onMessageSend={() => setStreamEvents([])}
            />
          </div>
        </aside>
      </div>
      {pendingDiff && (
        <DiffViewer
          diff={pendingDiff}
          sessionId={sessionId}
          onClose={() => setPendingDiff(null)}
          onApplied={handlePatchApplied}
        />
      )}
    </div>
  );
}
