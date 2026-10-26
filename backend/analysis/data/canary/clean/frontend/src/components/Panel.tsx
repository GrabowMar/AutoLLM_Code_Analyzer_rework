// CANARY FIXTURE: idiomatic React + TypeScript component (analyzer negative control).
import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  children: ReactNode;
}

function PanelHeading({ title }: { title: string }) {
  return <h2 className="panel-heading">{title}</h2>;
}

export default function Panel({ title, children }: PanelProps) {
  return (
    <section className="panel">
      <PanelHeading title={title} />
      {children}
    </section>
  );
}
