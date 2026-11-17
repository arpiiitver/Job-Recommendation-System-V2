"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { aiShortlist, getEmployerJobs } from "@/lib/api";
import { JobDetail, ShortlistCandidate, ShortlistResponse } from "@/lib/types";
import { EmployerLoading, useEmployerAuth } from "@/lib/use-employer-auth";
import { fitBand } from "@/components/stage-pill";
import {
  ArrowLeft,
  Bot,
  Briefcase,
  FileText,
  Info,
  Loader2,
  Lock,
  Mail,
  MapPin,
  ShieldCheck,
  Sparkles,
  UserCheck,
  UserSearch,
  Users,
} from "lucide-react";

const JD_MIN = 40;
const JD_MAX = 20000;
const TOP_K_OPTIONS = [5, 10, 15, 20, 30];

function initialsOf(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

function Chip({ label, tone }: { label: string; tone: "teal" | "rose" | "sky" | "zinc" }) {
  const cls = {
    teal: "bg-teal-500/10 text-teal-300 ring-teal-500/30",
    rose: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
    sky: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
    zinc: "bg-zinc-800/60 text-zinc-300 ring-zinc-700",
  }[tone];
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${cls}`}>
      {label}
    </span>
  );
}

function ChipRow({
  label,
  items,
  tone,
  title,
}: {
  label: string;
  items: string[];
  tone: "teal" | "rose" | "sky" | "zinc";
  title?: string;
}) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span
        title={title}
        className="text-[10px] font-semibold uppercase tracking-wider text-zinc-600"
      >
        {label}
      </span>
      {items.map((s) => (
        <Chip key={s} label={s} tone={tone} />
      ))}
    </div>
  );
}

function SourceLabel({ source }: { source: "llm" | "template" }) {
  return source === "llm" ? (
    <span className="inline-flex items-center gap-1 rounded bg-teal-500/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-teal-300 ring-1 ring-inset ring-teal-500/30">
      <Bot className="h-2.5 w-2.5" />
      AI-written
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-zinc-400 ring-1 ring-inset ring-zinc-700">
      <FileText className="h-2.5 w-2.5" />
      Template
    </span>
  );
}

function CandidateCard({ c }: { c: ShortlistCandidate }) {
  const band = fitBand(c.fit_score);
  return (
    <li className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-5 transition-colors hover:border-zinc-700/70">
      <div className="flex flex-wrap items-start gap-4">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-xs font-bold text-zinc-400">
          {c.rank}
        </div>
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-teal-400/80 to-cyan-600/80 text-sm font-bold text-zinc-950">
          {initialsOf(c.name) || "?"}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-zinc-100">{c.name}</span>
            {c.is_applicant && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
                <UserCheck className="h-3 w-3" />
                Applicant
              </span>
            )}
            {c.years_of_experience != null && (
              <span className="text-xs text-zinc-500">{c.years_of_experience} yrs</span>
            )}
          </div>
          {c.headline && <div className="mt-0.5 text-sm text-zinc-400">{c.headline}</div>}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
            {c.location && (
              <span className="inline-flex items-center gap-1">
                <MapPin className="h-3 w-3" />
                {c.location}
              </span>
            )}
            {c.is_applicant && c.email && (
              <a
                href={`mailto:${c.email}`}
                className="inline-flex items-center gap-1 hover:text-teal-400"
              >
                <Mail className="h-3 w-3" />
                {c.email}
              </a>
            )}
            {!c.is_applicant && (
              <span className="inline-flex items-center gap-1 text-zinc-600">
                <Lock className="h-3 w-3" />
                Contact hidden until they apply
              </span>
            )}
          </div>

          {/* explanation */}
          <div className="mt-3 space-y-2 rounded-lg border border-zinc-800/70 bg-zinc-950/40 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                Why it fits
              </span>
              <SourceLabel source={c.explanation_source} />
            </div>
            <p className="text-xs leading-relaxed text-zinc-300">{c.why_fit}</p>
            {c.gaps && (
              <>
                <span className="block text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
                  Gaps
                </span>
                <p className="text-xs leading-relaxed text-zinc-400">{c.gaps}</p>
              </>
            )}
          </div>

          <div className="mt-3 space-y-1.5">
            <ChipRow label="Matched" items={c.matched_skills} tone="teal" />
            <ChipRow label="Missing" items={c.missing_required} tone="rose" />
            <ChipRow
              label="Adjacent"
              items={c.transferable}
              tone="sky"
              title="Adjacent skills (skill2vec) the candidate could transfer into"
            />
          </div>
        </div>

        <div
          className={`flex shrink-0 flex-col items-center gap-1 rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-3 ring-1 ring-inset ${band.ring}`}
        >
          <div className={`text-3xl font-bold tabular-nums ${band.text}`}>
            {c.fit_score.toFixed(1)}
          </div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Fit score
          </div>
          <div className={`text-[11px] font-medium ${band.text}`}>{band.label}</div>
        </div>
      </div>
    </li>
  );
}

function ParsedRolePanel({ r }: { r: ShortlistResponse }) {
  return (
    <div className="mt-8 rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            <Briefcase className="h-3.5 w-3.5 text-teal-400" />
            Parsed role
          </div>
          <h2 className="mt-1 line-clamp-2 break-words text-lg font-semibold text-zinc-100">{r.title || "Untitled role"}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
            {r.seniority && <span className="capitalize">{r.seniority}</span>}
            {r.years_min != null && <span>{r.years_min}+ yrs</span>}
            <span>
              Pool: <span className="font-semibold text-zinc-300">{r.pool_size}</span> candidate
              {r.pool_size === 1 ? "" : "s"}
            </span>
          </div>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[11px] font-medium ring-1 ring-inset ${
            r.structured_by === "llm+rules"
              ? "bg-teal-500/10 text-teal-300 ring-teal-500/30"
              : "bg-zinc-950/70 text-zinc-400 ring-zinc-800"
          }`}
        >
          {r.structured_by === "llm+rules" ? (
            <Sparkles className="h-3.5 w-3.5" />
          ) : (
            <FileText className="h-3.5 w-3.5" />
          )}
          {r.structured_by === "llm+rules" ? "Parsed by AI + rules" : "Parsed by rules"}
        </span>
      </div>

      <div className="mt-4 space-y-2 border-t border-zinc-800/70 pt-4">
        <ChipRow label="Required" items={r.required_skills} tone="teal" />
        <ChipRow label="Preferred" items={r.preferred_skills} tone="zinc" />
        {r.required_skills.length === 0 && r.preferred_skills.length === 0 && (
          <p className="text-xs text-zinc-500">No skills recognised in this description.</p>
        )}
      </div>

      {r.dropped_llm_skills.length > 0 && (
        <div className="mt-3 flex items-start gap-2 text-[11px] leading-relaxed text-zinc-500">
          <ShieldCheck className="mt-px h-3.5 w-3.5 shrink-0 text-zinc-500" />
          <span>
            Ignored {r.dropped_llm_skills.length} AI-suggested skill
            {r.dropped_llm_skills.length === 1 ? "" : "s"} not found in your text:{" "}
            <span className="text-zinc-400">{r.dropped_llm_skills.join(", ")}</span>
          </span>
        </div>
      )}
    </div>
  );
}

function ShortlistInner() {
  const searchParams = useSearchParams();
  const presetPosting = searchParams.get("posting") ?? "";
  const { token, ready, isLoading: authLoading } = useEmployerAuth();

  const [jobs, setJobs] = useState<JobDetail[]>([]);
  const [postingId, setPostingId] = useState(presetPosting);
  const [jdText, setJdText] = useState("");
  const [title, setTitle] = useState("");
  const [location, setLocation] = useState("");
  const [topK, setTopK] = useState(10);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ShortlistResponse | null>(null);

  useEffect(() => {
    if (!ready || !token) return;
    let cancelled = false;
    getEmployerJobs(token)
      .then((list) => !cancelled && setJobs(list))
      .catch(() => !cancelled && setJobs([]));
    return () => {
      cancelled = true;
    };
  }, [ready, token]);

  if (authLoading || !ready) return <EmployerLoading />;

  const jdLen = jdText.trim().length;
  const canSubmit = !running && (postingId !== "" || jdLen >= JD_MIN) && jdText.length <= JD_MAX;
  const selectedJob = jobs.find((j) => j.id === postingId);

  const run = async () => {
    if (!token || !canSubmit) return;
    setRunning(true);
    setError(null);
    try {
      setResult(
        await aiShortlist(
          {
            jd_text: jdLen > 0 ? jdText.trim() : undefined,
            posting_id: postingId || undefined,
            title: title.trim() || undefined,
            location: location.trim() || undefined,
            top_k: topK,
          },
          token,
        ),
      );
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : "Failed to build shortlist");
    } finally {
      setRunning(false);
    }
  };

  const inputCls =
    "w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-teal-500/60 focus:outline-none";

  return (
    <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6 lg:px-8">
      <Link
        href="/hiring"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-zinc-400 hover:text-zinc-200"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to hiring
      </Link>

      <div className="mt-4">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-teal-400">
          <UserSearch className="h-4 w-4" />
          AI shortlist
        </div>
        <h1 className="mt-1 text-2xl font-bold tracking-tight text-zinc-100 sm:text-3xl">
          Find candidates for a role
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-zinc-400">
          Paste a role description (or pick one of your postings). We parse it into skills and
          seniority, score opted-in candidates with the same matching engine seekers use, and
          explain each fit. Scores are assistive only — nothing here rejects anyone.
        </p>
      </div>

      {/* ---------- Form ---------- */}
      <div className="mt-6 rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-5">
        <label className="block">
          <span className="mb-1.5 flex items-center justify-between text-[11px] font-medium uppercase tracking-wider text-zinc-400">
            <span>Role description</span>
            <span
              className={`font-mono normal-case tracking-normal ${
                jdText.length > JD_MAX ? "text-rose-400" : "text-zinc-600"
              }`}
            >
              {jdText.length.toLocaleString()} / {JD_MAX.toLocaleString()}
            </span>
          </span>
          <textarea
            value={jdText}
            onChange={(e) => setJdText(e.target.value)}
            rows={10}
            placeholder="e.g. We're hiring a backend engineer with 3+ years of Python, FastAPI and PostgreSQL experience. Docker and AWS are a plus…"
            className={`${inputCls} resize-y leading-relaxed`}
          />
        </label>
        {postingId === "" && jdLen > 0 && jdLen < JD_MIN && (
          <p className="mt-1 text-[11px] text-amber-400">
            Add a bit more detail — at least {JD_MIN} characters.
          </p>
        )}

        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block lg:col-span-2">
            <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-zinc-400">
              Or use one of my postings
            </span>
            <select
              value={postingId}
              onChange={(e) => setPostingId(e.target.value)}
              className={inputCls}
            >
              <option value="">— None (use the text above) —</option>
              {jobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {j.title} · {j.status}
                </option>
              ))}
              {presetPosting && !jobs.some((j) => j.id === presetPosting) && (
                <option value={presetPosting}>Posting from link</option>
              )}
            </select>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-zinc-400">
              Title (optional)
            </span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={selectedJob?.title ?? "Backend Engineer"}
              className={inputCls}
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-zinc-400">
              Location (optional)
            </span>
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder={selectedJob?.location ?? "Bengaluru"}
              className={inputCls}
            />
          </label>
        </div>
        {postingId !== "" && (
          <p className="mt-2 text-[11px] text-zinc-500">
            Using {selectedJob ? <span className="text-zinc-300">{selectedJob.title}</span> : "the selected posting"}
            {jdLen > 0 ? " together with the text above." : " — its description and skills drive the search."}{" "}
            Its applicants are included and marked.
          </p>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-zinc-800/70 pt-4">
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            Show top
            <select
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
              className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-200"
            >
              {TOP_K_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            candidates
          </label>
          <button
            type="button"
            onClick={run}
            disabled={!canSubmit}
            className="ml-auto inline-flex items-center gap-2 rounded-xl bg-teal-500 px-4 py-2.5 text-sm font-semibold text-zinc-950 shadow-lg shadow-teal-500/25 transition-all hover:bg-teal-400 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            {running ? "Finding candidates…" : "Find candidates"}
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-6 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {running && !result && <div className="mt-8 h-72 animate-pulse rounded-xl bg-zinc-900/50" />}

      {result && (
        <div className={running ? "opacity-60 transition-opacity" : undefined}>
          <ParsedRolePanel r={result} />

          {result.privacy_note && (
            <div className="mt-4 flex items-start gap-2 rounded-xl border border-teal-500/20 bg-teal-500/5 p-4 text-xs text-teal-300/90">
              <Lock className="mt-px h-4 w-4 shrink-0 text-teal-400" />
              {result.privacy_note}
            </div>
          )}

          {result.candidates.length === 0 ? (
            <div className="mt-6 rounded-xl border border-dashed border-zinc-800 bg-zinc-900/30 p-12 text-center">
              <Users className="mx-auto h-8 w-8 text-zinc-600" />
              <p className="mt-3 text-sm font-medium text-zinc-300">No matching candidates yet</p>
              <p className="mx-auto mt-1 max-w-md text-xs text-zinc-500">
                Only job seekers who opted in to recruiter discovery (plus your own applicants)
                appear here. Try broadening the description, or check back as more candidates
                opt in.
              </p>
            </div>
          ) : (
            <>
              <div className="mt-6 flex items-center gap-2 text-xs text-zinc-500">
                <Info className="h-3.5 w-3.5" />
                Top {result.candidates.length} of {result.pool_size} — ranked by fit score
              </div>
              <ol className="mt-3 space-y-3">
                {result.candidates.map((c) => (
                  <CandidateCard key={c.profile_id} c={c} />
                ))}
              </ol>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function ShortlistPage() {
  return (
    <Suspense fallback={<EmployerLoading />}>
      <ShortlistInner />
    </Suspense>
  );
}
