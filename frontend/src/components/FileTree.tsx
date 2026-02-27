import { useEffect, useState } from 'react';
import type { TreeEntry } from '../api/agent';
import { getRepoTree } from '../api/agent';

interface FileTreeProps {
  sessionId: string | null;
  onSelectFile: (path: string) => void;
  selectedPath: string | null;
  treeRefreshKey?: number;
}

function TreeRow({
  entry,
  level,
  onSelect,
  selectedPath,
}: {
  entry: TreeEntry;
  level: number;
  onSelect: (path: string) => void;
  selectedPath: string | null;
}) {
  const isDir = !!entry.children;
  const isSelected = entry.path === selectedPath;

  return (
    <div style={{ marginLeft: level * 12 }}>
      <button
        type="button"
        onClick={() => !isDir && onSelect(entry.path)}
        style={{
          background: isSelected ? '#333' : 'transparent',
          border: 'none',
          color: '#d4d4d4',
          cursor: isDir ? 'default' : 'pointer',
          padding: '4px 8px',
          textAlign: 'left',
          width: '100%',
        }}
      >
        {isDir ? '📁 ' : '📄 '}
        {entry.name}
      </button>
      {entry.children?.map((child) => (
        <TreeRow
          key={child.path}
          entry={child}
          level={level + 1}
          onSelect={onSelect}
          selectedPath={selectedPath}
        />
      ))}
    </div>
  );
}

export default function FileTree({ sessionId, onSelectFile, selectedPath, treeRefreshKey = 0 }: FileTreeProps) {
  const [tree, setTree] = useState<TreeEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!sessionId) {
      setTree([]);
      return;
    }
    setLoading(true);
    getRepoTree(sessionId)
      .then((data) => {
        setTree(data.tree);
      })
      .catch(() => setTree([]))
      .finally(() => setLoading(false));
  }, [sessionId, treeRefreshKey]);

  if (!sessionId) return <div style={{ padding: 12, color: '#888' }}>Clone a repo to see files.</div>;
  if (loading) return <div style={{ padding: 12 }}>Loading tree…</div>;
  if (tree.length === 0) return <div style={{ padding: 12, color: '#888' }}>No files.</div>;

  return (
    <div style={{ padding: 8, overflow: 'auto' }}>
      {tree.map((entry) => (
        <TreeRow
          key={entry.path}
          entry={entry}
          level={0}
          onSelect={onSelectFile}
          selectedPath={selectedPath}
        />
      ))}
    </div>
  );
}
