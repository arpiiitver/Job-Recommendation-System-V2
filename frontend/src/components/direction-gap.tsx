"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRightLeft,
  Briefcase,
  CheckCircle2,
  Compass,
  ExternalLink,
  Loader2,
  RefreshCw,
  Route,
  Target,
} from "lucide-react";
import { getDirectionGap } from "@/lib/api";
import { DirectionGap as DirectionGapData, DirectionGapResponse, SkillDemand } from "@/lib/types";

const STATUS_STYLE: Record<SkillDemand["status"], string> = {
  have: "border-emerald-500/20 bg-emerald-500/10 text-emerald-300",
  transferable: "border-sky-500/20 bg-sky-500/10 text-sky-300",
  missing: "border-amber-500/20 bg-amber-500/10 text-amber-300",
};

const STATUS_LABEL: Record<SkillDemand["status"], string> = {
  have: "you have it",
  transferable: "transferable from a related skill",
  missing: "missing from your profile",
};

function readinessTone(score: number) {
  if (score >= 70) return { text: "text-emerald-300", bar: "bg-emerald-400" };
  if (score >= 40) return { text: "text-amber-300", bar: "bg-amber-400" };
  return { text: "text-rose-300", bar: "bg-rose-400" };
}

/** One target's demand-vs-profile card (also rendered inline by the Copilot chat). */
export function TargetCard({ gap }: { gap: DirectionGapData }) {
  const readiness = typeof gap.readiness === "number" ? Math.round(gap.readiness) : null;
  const tone = readiness !== null ? readinessTone(readiness) : null;

  return (
    <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-bold text-zinc-100">{gap.target}</h3>
            <span className="rounded-full bg-zinc-800/80 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-300">
              {gap.kind === "family" ? "role family" : "target role"}
            </span>
          </div>
          <p className="mt-0.5 text-[11px] text-zinc-500">
            {gap.jobs_analyzed} job{gap.jobs_analyzed === 1 ? "" : "s"} analysed
            {gap.sources.length > 0 && ` · ${gap.sources.join(" + ")}`}
          </p>
        </div>
        <div className="shrink-0 text-right">
          {readiness !== null ? (
            <>
              <span className={`text-lg font-black ${tone?.text}`}>{readiness}%</span>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                ready
              </p>
            </>
          ) : (
            <span className="text-xs text-zinc-500">n/a</span>
          )}
        </div>
      </div>

      {readiness !== null && (
        <div
          className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800"
          role="meter"
          aria-valuenow={readiness}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Readiness for ${gap.target}`}
        >
          <div
            className={`h-full rounded-full ${tone?.bar} transition-all`}
            style={{ width: `${Math.min(100, readiness)}%` }}
          />
        </div>
      )}

      {gap.skills.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Most-demanded skills
          </p>
          <div className="flex flex-wrap gap-1.5">
            {gap.skills.map((s) => (
              <span
                key={s.skill}
                title={`${s.skill}: asked for by ${s.jobs} of ${gap.jobs_analyzed} jobs — ${STATUS_LABEL[s.status]}`}
                className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLE[s.status]}`}
              >
                {s.status === "have" && <CheckCircle2 className="h-3 w-3" />}
                {s.status === "transferable" && <ArrowRightLeft className="h-3 w-3" />}
                {s.status === "missing" && <AlertCircle className="h-3 w-3" />}
                {s.skill}
                <span className="tabular-nums opacity-70">{Math.round(s.share * 100)}%</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {gap.top_missing.length > 0 && (
        <Link
          href="/roadmap"
          className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-teal-400 underline-offset-4 hover:text-teal-300 hover:underline"
        >
          <Route className="h-3.5 w-3.5" />
          Close the gap: {gap.top_missing.slice(0, 3).map((d) => d.skill).join(", ")}
        </Link>
      )}

      {gap.note && <p className="mt-3 text-[11px] leading-relaxed text-zinc-500">{gap.note}</p>}

      {gap.sample_jobs.length > 0 && (
        <div className="mt-3 border-t border-zinc-800/60 pt-3">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Example postings
          </p>
          <ul className="space-y-1">
            {gap.sample_jobs.slice(0, 3).map((job, i) => {
              const label = (
                <>
                  <span className="truncate font-medium">{job.title || "Untitled role"}</span>
                  {job.company && <span className="truncate text-zinc-500">· {job.company}</span>}
                </>
              );
              const cls =
                "flex items-center gap-1.5 text-xs text-zinc-300 hover:text-teal-300 transition-colors";
              return (
                <li key={job.id ?? job.url ?? i}>
                  {job.id ? (
                    <Link href={`/jobs/${job.id}`} className={cls}>
                      <Briefcase className="h-3 w-3 shrink-0 text-zinc-500" />
                      {label}
                    </Link>
                  ) : job.url ? (
                    <a href={job.url} target="_blank" rel="noopener noreferrer" className={cls}>
                      <ExternalLink className="h-3 w-3 shrink-0 text-sky-400" />
                      {label}
                    </a>
                  ) : (
                    <span className="flex items-center gap-1.5 text-xs text-zinc-400">
                      <Briefcase className="h-3 w-3 shrink-0 text-zinc-500" />
                      {label}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

type Result = { key: string; data?: DirectionGapResponse; error?: string };

/**
 * "What does my target role demand?" — one card per career-direction target
 * (typed roles/domains + target family). Bump `refreshKey` to re-fetch, e.g.
 * after the profile is saved.
 */
export default function DirectionGap({
  token,
  refreshKey = 0,
  className = "",
}: {
  token: string | null;
  refreshKey?: number;
  className?: string;
}) {
  const [live, setLive] = useState(true);
  const [nonce, setNonce] = useState(0);
  const [result, setResult] = useState<Result | null>(null);
  const requestKey = `${refreshKey}:${nonce}:${live}`;

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    getDirectionGap(token, live)
      .then((data) => {
        if (!cancelled) setResult({ key: requestKey, data });
      })
      .catch((err) => {
        if (!cancelled) {
          setResult({
            key: requestKey,
            error: err instanceof Error ? err.message : "Failed to load direction gap",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token, live, requestKey]);

  if (!token) return null;
  const loading = !result || result.key !== requestKey;
  const data = result?.data;

  return (
    <section
      aria-label="Career direction gap"
      className={`rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-6 backdrop-blur ${className}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Target className="mt-0.5 h-5 w-5 shrink-0 text-teal-400" />
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">
              What does my target role demand?
            </h2>
            <p className="mt-1 text-xs text-zinc-400">
              Skills the market asks for in each career-direction target, compared with your
              profile. Readiness weights each skill by how often it&apos;s requested.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-300">
            <input
              type="checkbox"
              checked={live}
              onChange={(e) => setLive(e.target.checked)}
              className="rounded border-zinc-700 bg-zinc-900 text-teal-500 focus:ring-teal-500"
            />
            Include live postings
          </label>
          <button
            type="button"
            onClick={() => setNonce((n) => n + 1)}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900/70 px-3 py-1.5 text-xs font-semibold text-zinc-300 transition-colors hover:border-teal-500/50 hover:text-teal-300 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      <div className="mt-5">
        {loading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin text-teal-400" />
            Analysing what your target directions demand…
          </div>
        ) : result?.error ? (
          <div className="flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-400">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <span>{result.error}</span>
          </div>
        ) : data && data.targets.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {data.targets.map((gap) => (
              <TargetCard key={`${gap.kind}:${gap.target}`} gap={gap} />
            ))}
          </div>
        ) : (
          <div className="flex items-start gap-2 rounded-lg border border-dashed border-zinc-800 bg-zinc-900/30 p-4 text-sm text-zinc-400">
            <Compass className="mt-0.5 h-4 w-4 shrink-0 text-zinc-500" />
            <span>
              {data?.message ||
                "Add a target role family or target roles/domains in Career Direction to see what they demand."}
            </span>
          </div>
        )}
      </div>
    </section>
  );
}
