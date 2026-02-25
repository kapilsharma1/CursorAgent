import { useEffect, useRef, useState } from 'react';
import Editor from '@monaco-editor/react';
import { getFile } from '../api/agent';

interface CodeEditorProps {
  sessionId: string | null;
  filePath: string | null;
  references: { file: string; line: number }[];
  refreshKey?: number;
}

export default function CodeEditor({ sessionId, filePath, references, refreshKey = 0 }: CodeEditorProps) {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const editorRef = useRef<Parameters<NonNullable<Parameters<typeof Editor>[0]['onMount']>>[0] | null>(null);
  const decorationsRef = useRef<string[]>([]);

  useEffect(() => {
    if (!sessionId || !filePath) {
      setContent('');
      return;
    }
    setLoading(true);
    getFile(sessionId, filePath)
      .then((data) => setContent(data.content))
      .catch(() => setContent(''))
      .finally(() => setLoading(false));
  }, [sessionId, filePath, refreshKey]);

  // Apply line highlights when references or file change; run after content is loaded so editor is ready
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || !filePath || !content) return;
    const linesToHighlight = references
      .filter((r) => r.file === filePath)
      .map((r) => r.line);
    const prev = decorationsRef.current;
    if (prev.length) {
      editor.deltaDecorations(prev, []);
      decorationsRef.current = [];
    }
    if (linesToHighlight.length === 0) return;
    const newDecos = editor.deltaDecorations(
      [],
      linesToHighlight.map((line) => ({
        range: { startLineNumber: line, startColumn: 1, endLineNumber: line, endColumn: 4096 },
        options: {
          isWholeLine: true,
          className: 'reference-line-highlight',
          linesDecorationsClassName: 'reference-gutter',
        },
      }))
    );
    decorationsRef.current = newDecos;
    const first = linesToHighlight[0];
    if (first != null) editor.revealLineInCenter(first);
  }, [filePath, references, content]);

  const handleMount = (editor: Parameters<NonNullable<Parameters<typeof Editor>[0]['onMount']>>[0]) => {
    editorRef.current = editor;
  };

  if (!sessionId) {
    return <div style={{ padding: 24, color: '#888' }}>Open a repo and select a file.</div>;
  }
  if (!filePath) {
    return <div style={{ padding: 24, color: '#888' }}>Select a file from the tree.</div>;
  }
  if (loading) {
    return <div style={{ padding: 24 }}>Loading…</div>;
  }

  return (
    <div style={{ height: '100%' }}>
      <div style={{ padding: '4px 12px', borderBottom: '1px solid #333', fontSize: 12 }}>{filePath}</div>
      <Editor
        height="100%"
        defaultLanguage="plaintext"
        value={content}
        onChange={(v) => setContent(v ?? '')}
        onMount={handleMount}
        theme="vs-dark"
        options={{ readOnly: true, minimap: { enabled: false } }}
      />
      <style>{`
        .reference-line-highlight { background: rgba(255, 200, 0, 0.15); }
        .reference-gutter { background: rgba(255, 200, 0, 0.4); }
      `}</style>
    </div>
  );
}
