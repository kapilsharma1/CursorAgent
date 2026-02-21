/**
 * API client: clone repo, repo tree, file content, run agent (SSE), apply patch.
 */

const API_BASE = import.meta.env.VITE_API_URL || '/api';

export interface CloneRepoResponse {
  session_id: string;
  tree: TreeEntry[];
  message?: string;
}

export interface TreeEntry {
  name: string;
  path: string;
  children?: TreeEntry[];
}

export interface FileContentResponse {
  path: string;
  content: string;
}

export interface ApplyPatchResponse {
  success: boolean;
  message: string;
  updated_files?: Record<string, string>;
  error?: string;
}

export type StreamEvent =
  | { type: 'status'; message?: string }
  | { type: 'agent_step'; agent: 'Planner' | 'Coder' | 'Reviewer'; message?: string }
  | { type: 'retrieval'; files: string[] }
  | { type: 'diff'; diff: string }
  | { type: 'final'; message?: string; references?: { file: string; line: number }[] };

export async function cloneRepo(repoUrl: string): Promise<CloneRepoResponse> {
  const res = await fetch(`${API_BASE}/clone-repo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_url: repoUrl }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((err as { detail?: string }).detail || 'Clone failed');
  }
  return res.json();
}

export async function getRepoTree(sessionId: string): Promise<{ tree: TreeEntry[] }> {
  const res = await fetch(`${API_BASE}/repo-tree?session_id=${encodeURIComponent(sessionId)}`);
  if (!res.ok) throw new Error('Failed to load repo tree');
  return res.json();
}

export async function getFile(sessionId: string, path: string): Promise<FileContentResponse> {
  const res = await fetch(
    `${API_BASE}/file?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`
  );
  if (!res.ok) throw new Error('Failed to load file');
  return res.json();
}

export function runAgentStream(
  sessionId: string,
  message: string,
  onEvent: (event: StreamEvent) => void
): () => void {
  const ac = new AbortController();
  (async () => {
    try {
      const res = await fetch(`${API_BASE}/run-agent/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, message }),
        signal: ac.signal,
      });
      if (!res.ok) throw new Error('Stream failed');
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      if (!reader) return;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6)) as StreamEvent;
              onEvent(event);
            } catch {
              // skip malformed
            }
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') onEvent({ type: 'status', message: (e as Error).message });
    }
  })();
  return () => ac.abort();
}

export async function applyPatch(sessionId: string, diff: string): Promise<ApplyPatchResponse> {
  const res = await fetch(`${API_BASE}/apply-patch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, diff, dry_run: false }),
  });
  return res.json();
}
