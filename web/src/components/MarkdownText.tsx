import type { ReactNode } from "react";

interface MarkdownTextProps {
  text: string;
  empty?: ReactNode;
  className?: string;
}

function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  for (const [index, part] of parts.entries()) {
    if (!part) continue;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      nodes.push(<strong key={index}>{part.slice(2, -2)}</strong>);
    } else {
      nodes.push(part);
    }
  }
  return nodes;
}

export function MarkdownText({ text, empty, className }: MarkdownTextProps): JSX.Element {
  const value = text.trim();
  if (!value) return <>{empty ?? null}</>;
  const lines = value.split(/\n+/);
  return (
    <span className={className}>
      {lines.map((line, index) => (
        <span key={index}>
          {index > 0 && <br />}
          {inline(line)}
        </span>
      ))}
    </span>
  );
}
