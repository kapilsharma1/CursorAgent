import { useState } from 'react';
import type { TreeEntry } from '../api/agent';
import { cloneRepo } from '../api/agent';

interface RepoLoaderProps {
  onLoaded: (sessionId: string, tree: TreeEntry[]) => void;
}

export default function RepoLoader({ onLoaded }: RepoLoaderProps) {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await cloneRepo(url.trim());
      onLoaded(data.session_id, data.tree);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <input
        type="url"
        placeholder="https://github.com/owner/repo"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        disabled={loading}
        style={{ flex: 1, minWidth: 200, padding: '8px 12px', borderRadius: 6, border: '1px solid #444' }}
      />
      <button type="submit" disabled={loading} style={{ padding: '8px 16px', borderRadius: 6 }}>
        {loading ? 'Cloning…' : 'Clone & load'}
      </button>
      {error && <span style={{ color: '#f88' }}>{error}</span>}
    </form>
  );
}
