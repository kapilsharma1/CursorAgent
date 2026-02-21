interface AgentStatusBarProps {
  agent: string | null;
}

export default function AgentStatusBar({ agent }: AgentStatusBarProps) {
  return (
    <div
      style={{
        padding: '4px 12px',
        fontSize: 12,
        background: '#252526',
        borderBottom: '1px solid #333',
        color: agent ? '#d4d4d4' : '#888',
      }}
    >
      {agent ? `Agent: ${agent}` : 'Idle'}
    </div>
  );
}
