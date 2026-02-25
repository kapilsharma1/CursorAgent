/**
 * API client: clone repo, repo tree, file content, run agent (SSE), apply patch.
 */

import { logger } from '../lib/logger';

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
  logger.info('API', 'cloneRepo', { repoUrl });
  const res = await fetch(`${API_BASE}/clone-repo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_url: repoUrl }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const msg = (err as { detail?: string }).detail || 'Clone failed';
    logger.error('API', 'cloneRepo failed', { repoUrl, status: res.status, detail: msg });
    throw new Error(msg);
  }
  const data = await res.json();
  logger.info('API', 'cloneRepo success', { sessionId: data.session_id, treeEntries: data.tree?.length });
  return data;
}

export async function getRepoTree(sessionId: string): Promise<{ tree: TreeEntry[] }> {
  logger.debug('API', 'getRepoTree', { sessionId });
  const res = await fetch(`${API_BASE}/repo-tree?session_id=${encodeURIComponent(sessionId)}`);
  if (!res.ok) {
    logger.error('API', 'getRepoTree failed', { sessionId, status: res.status });
    throw new Error('Failed to load repo tree');
  }
  const data = await res.json();
  logger.debug('API', 'getRepoTree success', { sessionId, entries: data.tree?.length });
  return data;
}

export async function getFile(sessionId: string, path: string): Promise<FileContentResponse> {
  logger.debug('API', 'getFile', { sessionId, path });
  const res = await fetch(
    `${API_BASE}/file?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`
  );
  if (!res.ok) {
    logger.warn('API', 'getFile failed', { sessionId, path, status: res.status });
    throw new Error('Failed to load file');
  }
  const data = await res.json();
  logger.debug('API', 'getFile success', { sessionId, path, contentLen: data.content?.length });
  return data;
}

export function runAgentStream(
  sessionId: string,
  message: string,
  onEvent: (event: StreamEvent) => void,
  searchMode?: boolean
): () => void {
  logger.info('Agent', 'runAgentStream start', { sessionId, messageLen: message.length, searchMode });
  const ac = new AbortController();
  let eventCount = 0;
  (async () => {
    try {
      const res = await fetch(`${API_BASE}/run-agent/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          message,
          search_mode: searchMode === true,
        }),
        signal: ac.signal,
      });
      if (!res.ok) {
        logger.error('Agent', 'runAgentStream fetch failed', { sessionId, status: res.status });
        throw new Error('Stream failed');
      }
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      if (!reader) {
        logger.warn('Agent', 'runAgentStream no response body');
        return;
      }
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
              eventCount += 1;
              logger.debug('Agent', 'stream event', { sessionId, type: event.type, index: eventCount });
              onEvent(event);
            } catch {
              // skip malformed
            }
          }
        }
      }
      logger.info('Agent', 'runAgentStream completed', { sessionId, totalEvents: eventCount });
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        logger.error('Agent', 'runAgentStream error', { sessionId, error: (e as Error).message });
        onEvent({ type: 'status', message: (e as Error).message });
      } else {
        logger.debug('Agent', 'runAgentStream aborted', { sessionId });
      }
    }
  })();
  return () => ac.abort();
}

export async function applyPatch(sessionId: string, diff: string): Promise<ApplyPatchResponse> {
  logger.info('API', 'applyPatch', { sessionId, diffLen: diff.length });
  const res = await fetch(`${API_BASE}/apply-patch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, diff, dry_run: false }),
  });
  const data = await res.json();
  if (!data.success) {
    logger.warn('API', 'applyPatch failed', { sessionId, error: data.error });
  } else {
    logger.info('API', 'applyPatch success', { sessionId, updatedFiles: data.updated_files && Object.keys(data.updated_files) });
  }
  return data;
}
