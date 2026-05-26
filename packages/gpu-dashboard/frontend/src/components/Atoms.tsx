import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

// Small atoms shared by every page. Same shape as slurm-mgr's
// components/Button + Badge + Card + Table, condensed into one file
// since gpu-watch has fewer pages.

type Variant = "primary" | "secondary" | "danger" | "ghost";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary:   "bg-accent-teal/15 text-accent-teal border-accent-teal/30 hover:bg-accent-teal/25",
  secondary: "bg-dark-panel text-text-primary border-border-subtle hover:border-border-active",
  danger:    "bg-accent-red/15 text-accent-red border-accent-red/30 hover:bg-accent-red/25",
  ghost:     "bg-transparent text-text-secondary border-transparent hover:text-text-primary hover:bg-dark-panel",
};
const SIZES: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs rounded-sm",
  md: "h-9 px-3.5 text-sm rounded-md",
};

export function Button({
  variant = "secondary", size = "md", loading, icon, children, className, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: Size; loading?: boolean; icon?: ReactNode;
}) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 font-display font-medium",
        "transition-colors disabled:opacity-50 disabled:cursor-not-allowed border",
        "focus:outline-none focus:ring-1 focus:ring-accent-teal/40",
        VARIANTS[variant], SIZES[size], className,
      )}
      disabled={loading || rest.disabled}
      {...rest}
    >
      {loading ? (
        <span className="inline-block h-3 w-3 rounded-full border-2 border-current border-t-transparent animate-spin" />
      ) : icon}
      {children}
    </button>
  );
}

type Tone = "neutral" | "green" | "red" | "yellow" | "blue" | "orange" | "purple" | "teal";
const TONES: Record<Tone, string> = {
  neutral: "bg-dark-panel text-text-secondary border-border-subtle",
  green:   "bg-accent-green/10 text-accent-green border-accent-green/25",
  red:     "bg-accent-red/10 text-accent-red border-accent-red/25",
  yellow:  "bg-accent-yellow/10 text-accent-yellow border-accent-yellow/25",
  blue:    "bg-accent-blue/10 text-accent-blue border-accent-blue/25",
  orange:  "bg-accent-orange/10 text-accent-orange border-accent-orange/25",
  purple:  "bg-accent-purple/10 text-accent-purple border-accent-purple/25",
  teal:    "bg-accent-teal/10 text-accent-teal border-accent-teal/25",
};

export function Badge({
  tone = "neutral", children, className,
}: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span className={clsx(
      "inline-flex items-center px-1.5 py-0.5 text-[10.5px] font-mono uppercase tracking-[0.5px] rounded-sm border",
      TONES[tone], className,
    )}>{children}</span>
  );
}

export function Card({
  title, actions, children, className,
}: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx(
      "bg-dark-secondary border border-border-subtle rounded-md overflow-hidden flex flex-col",
      className,
    )}>
      {(title || actions) && (
        <header className="flex items-center justify-between px-3 py-2 border-b border-border-subtle bg-dark-tertiary">
          <h2 className="font-display text-[13px] font-semibold text-text-primary uppercase tracking-[0.5px]">{title}</h2>
          {actions && <div className="flex items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className="flex-1 min-h-0 overflow-auto">{children}</div>
    </section>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="h-full grid place-items-center text-text-muted font-mono text-xs px-6 text-center">
      {message}
    </div>
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="m-3 p-3 bg-accent-red/10 border border-accent-red/30 rounded-sm font-mono text-xs text-accent-red whitespace-pre-wrap">
      {message}
    </div>
  );
}

// Temperature → tone heuristic. 80C and up is yellow (warm), 90+ is red.
export function tempTone(c: number | null | undefined): Tone {
  if (c == null) return "neutral";
  if (c >= 90) return "red";
  if (c >= 80) return "orange";
  if (c >= 70) return "yellow";
  return "green";
}

export type Column<T> = {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  align?: "left" | "right" | "center";
};

export function Table<T>({
  columns, rows, rowKey, empty,
}: { columns: Column<T>[]; rows: T[]; rowKey: (r: T, i: number) => string; empty?: string }) {
  if (rows.length === 0) {
    return (
      <div className="h-full grid place-items-center text-text-muted font-mono text-xs">
        {empty ?? "no rows"}
      </div>
    );
  }
  return (
    <table className="w-full font-mono text-xs">
      <thead className="sticky top-0 bg-dark-tertiary z-10">
        <tr>
          {columns.map((c) => (
            <th key={c.key} className={clsx(
              "text-left font-semibold text-text-secondary uppercase tracking-[0.5px] px-3 py-2 border-b border-border-subtle",
              c.align === "right" && "text-right",
              c.align === "center" && "text-center",
            )}>{c.header}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={rowKey(r, i)} className="hover:bg-dark-panel/60 transition-colors border-b border-border-subtle/50">
            {columns.map((c) => (
              <td key={c.key} className={clsx(
                "px-3 py-2 text-text-primary align-top",
                c.align === "right" && "text-right",
                c.align === "center" && "text-center",
              )}>{c.cell(r)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
