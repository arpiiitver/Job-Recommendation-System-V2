import { GraduationCap } from "lucide-react";

type Props = {
  level: string;
  explicit?: boolean | null;
  reason?: string | null;
  size?: "sm" | "md";
};

/**
 * Seniority badge backed by evidence: entry roles read "Fresher-friendly",
 * postings that state no seniority read "Level not stated" (instead of a
 * defaulted "mid"), and the reason ("posting says 'Freshers can apply'")
 * is shown on hover.
 */
export default function LevelBadge({ level, explicit, reason, size = "sm" }: Props) {
  const text = size === "sm" ? "text-[11px] px-2 py-0.5" : "text-xs px-2.5 py-0.5";
  const tip = reason ?? undefined;

  if (explicit === false) {
    return (
      <span title={tip} className={`rounded bg-zinc-800/60 font-medium text-zinc-400 ${text}`}>
        Level not stated
      </span>
    );
  }
  if (level === "entry") {
    return (
      <span
        title={tip}
        className={`inline-flex items-center gap-1 rounded-full bg-teal-500/10 font-medium text-teal-300 ring-1 ring-teal-500/30 ${text}`}
      >
        <GraduationCap className="h-3 w-3" />
        Fresher-friendly
      </span>
    );
  }
  return (
    <span title={tip} className={`rounded bg-zinc-800 font-medium text-zinc-300 capitalize ${text}`}>
      {size === "md" ? `${level} Level` : level}
    </span>
  );
}
