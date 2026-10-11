import { useState } from "react"
import type { Change, Step } from "../api"
import { stats } from "../lib/diff"
import { clock, duration, exact } from "../lib/time"
import { Pill, statusTone } from "./Chip"
import { Diff } from "./Diff"
import { Icon } from "./Icon"

const LABELS: Record<string, string> = {
  read: "Read",
  edit: "Edit",
  command: "Ran",
  test: "Test",
  agent: "Said",
  answer: "Answer",
  subagent: "Sub-agent",
  error: "Error",
  thinking: "Thought",
  user: "You",
  tool: "Tool",
  approval: "Approval",
}

/** A turn's steps on a vertical rail. The step that wrote the line stands out; with `originDiff`
 * it also opens its diff (off when the diff is shown elsewhere). Outputs and other diffs open on request. */
export function Timeline({ steps, color, cwd, originDiff = true, dense = false }: {
  steps: Step[]; color: string; cwd?: string | null; originDiff?: boolean; dense?: boolean
}) {
  if (dense) {
    return (
      <ol className="rounded-xl border border-edge py-1">
        {steps.map((step) => <DenseStep key={step.id} step={step} color={color} cwd={cwd} />)}
      </ol>
    )
  }
  return (
    <ol className="relative">
      <span className="absolute top-3 bottom-3 left-[13px] w-px bg-edge" aria-hidden="true" />
      {steps.map((step, i) => (
        <StepItem key={step.id} step={step} color={color} cwd={cwd} delay={i} originDiff={originDiff} />
      ))}
    </ol>
  )
}

/** A step on one line (time, icon, what it was, how it went); click it for the rest: the full message,
 * the command's output, the diff. */
function DenseStep({ step, color, cwd }: { step: Step; color: string; cwd?: string | null }) {
  const [open, setOpen] = useState(false)
  const failed = step.status === "failed" || step.status === "declined"
  const mono = (step.label === "command" || step.label === "test") && !step.note
  const isMessage = step.label === "agent" || step.label === "answer" || step.label === "thinking"
  const changes = step.changes ?? []
  const title = step.label === "edit" && changes.length ? null : step.note ?? (mono ? shorten(step.title, cwd) : step.title)
  const more = !!(step.output || (changes.length && !step.origin) || (isMessage && step.title.length > 90) || step.note)
  return (
    <li id={`step-${step.id}`} className={`scroll-mt-4 ${step.origin ? "tint" : ""}`} style={step.origin ? ({ "--c": color } as React.CSSProperties) : undefined}>
      <button
        type="button"
        disabled={!more}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-2.5 py-1 text-left enabled:hover:bg-sunken/60"
        aria-expanded={more ? open : undefined}
      >
        <span className="w-[52px] shrink-0 font-mono text-[10.5px] text-ink-3 tabular-nums">{clock(step.at)}</span>
        <span className={`shrink-0 ${failed ? "text-bad" : step.origin ? "" : "text-ink-3"}`} style={step.origin ? { color } : undefined}>
          <Icon name={step.label} size={13} />
        </span>
        <span className="flex min-w-0 flex-1 items-center gap-1.5">
          {title != null && (
            <span className={`truncate ${mono ? "font-mono text-[11.5px]" : "text-[12.5px]"} ${isMessage ? "text-ink-2" : "text-ink"}`}>
              {(isMessage ? title.replace(/`/g, "") : title).split("\n")[0]}
            </span>
          )}
          {changes.map((c) => <FileStat key={c.path} change={c} />)}
        </span>
        {step.origin && (
          <span className="shrink-0 rounded-full px-1.5 py-px text-[10px] font-semibold text-white" style={{ background: color }}>wrote this line</span>
        )}
        {step.label === "test" || step.label === "command" ? (
          step.summary ? (
            <span className="max-w-[45%] shrink-0">
              <Pill tone={step.label === "test" ? statusTone(step.status) : failed ? "bad" : "plain"} title={step.summary}>
                {step.label === "test" && <Icon name={step.status === "completed" ? "check" : "x"} size={11} />}
                {step.summary}
              </Pill>
            </span>
          ) : failed ? <Pill tone="bad">{step.status}</Pill> : null
        ) : step.status && step.status !== "completed" ? <Pill tone={statusTone(step.status)}>{step.status}</Pill> : null}
        <span className={`w-3 shrink-0 text-ink-3 ${more ? "" : "invisible"}`}><Icon name={open ? "down" : "chevron"} size={11} /></span>
      </button>
      {open && (
        <div className="animate-fade-in space-y-2 pr-3 pb-2 pl-[88px]">
          {isMessage && <Prose text={step.title} clamp={false} />}
          {step.note && <code className="block font-mono text-[11px] break-words whitespace-pre-wrap text-ink-3">$ {shorten(step.title, cwd)}</code>}
          {!step.origin && changes.map((c) => <Diff key={c.path} change={c} />)}
          {step.output && <Output text={step.output} cut={step.output_cut ?? 0} />}
        </div>
      )}
    </li>
  )
}

function FileStat({ change }: { change: Change }) {
  const { added, removed } = stats(change.lines)
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-sunken px-1.5 py-px font-mono text-[11px] text-ink">
      {change.rel.split("/").pop()}
      {added > 0 && <span className="text-add">+{added}</span>}
      {removed > 0 && <span className="text-del">−{removed}</span>}
    </span>
  )
}

function StepItem({ step, color, cwd, delay, originDiff }: { step: Step; color: string; cwd?: string | null; delay: number; originDiff: boolean }) {
  const [open, setOpen] = useState(false)
  const failed = step.status === "failed" || step.status === "declined"
  const isMessage = step.label === "agent" || step.label === "answer" || step.label === "thinking"
  const hasMore = !!(step.output || (step.changes?.length && !step.origin) || (isMessage && step.title.length > 220))
    && !(step.origin && !originDiff && !step.output)
  const title = step.label === "command" || step.label === "test" ? shorten(step.title, cwd) : step.title
  const took = duration(step.at, step.ended_at)

  return (
    <li
      id={`step-${step.id}`}
      className="animate-rise relative flex scroll-mt-4 gap-3 pb-3 [animation-fill-mode:backwards]"
      style={{ animationDelay: `${Math.min(delay, 12) * 22}ms` }}
    >
      <span
        className={`relative z-10 mt-0.5 flex size-[27px] shrink-0 items-center justify-center rounded-full border ${
          step.origin ? "border-transparent text-white shadow-[0_0_0_4px_var(--panel)]" : "border-edge bg-panel text-ink-2"
        }`}
        style={step.origin ? { background: color } : failed ? { color: "var(--bad)" } : undefined}
      >
        <Icon name={step.label} size={13} />
      </span>
      <div
        className={`min-w-0 flex-1 ${step.origin ? "tint -mx-1 rounded-xl px-3 py-2.5 ring-1" : "pt-1"}`}
        style={step.origin ? ({ "--c": color, "--tw-ring-color": `color-mix(in oklab, ${color} 35%, transparent)` } as React.CSSProperties) : undefined}
      >
        <div className="flex items-baseline gap-2">
          <span className="shrink-0 text-[11px] font-semibold tracking-wide text-ink-3 uppercase">{LABELS[step.label] ?? step.label}</span>
          {step.origin && (
            <span className="shrink-0 rounded-full px-1.5 py-px text-[10.5px] font-semibold text-white" style={{ background: color }}>
              wrote this line
            </span>
          )}
          <time className="ml-auto shrink-0 font-mono text-[10.5px] text-ink-3 tabular-nums" title={exact(step.at)}>
            {clock(step.at)}
            {took && took !== "0.0s" ? <span className="text-ink-3/70"> · {took}</span> : null}
          </time>
        </div>
        {isMessage ? (
          <Prose text={step.title} clamp={!open} />
        ) : step.note ? (
          <>
            <div className="mt-0.5 text-[12.5px] text-ink">{step.note}</div>
            <div className={`mt-0.5 font-mono text-[11px] break-words text-ink-3 ${open ? "" : "line-clamp-1"}`}>{title}</div>
          </>
        ) : (
          <div className={`mt-0.5 break-words ${step.label === "command" || step.label === "test" ? "font-mono text-[12px]" : "text-[12.5px]"} text-ink`}>
            {title}
          </div>
        )}
        {(step.summary || (step.status && step.status !== "completed")) && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {step.status && step.status !== "completed" && <Pill tone={statusTone(step.status)}>{step.status}</Pill>}
            {step.summary && (
              <Pill tone={step.label === "test" ? statusTone(step.status) : "plain"} title={step.summary}>
                {step.label === "test" && <Icon name={step.status === "completed" ? "check" : "x"} size={11} />}
                {step.summary}
              </Pill>
            )}
          </div>
        )}
        {step.origin &&
          originDiff &&
          step.changes?.map((change) => (
            <div key={change.path} className="mt-2">
              <Diff change={change} color={color} />
            </div>
          ))}
        {open && !step.origin && (
          <div className="animate-fade-in mt-2 space-y-2">
            {step.changes?.map((change) => <Diff key={change.path} change={change} />)}
            {step.output && <Output text={step.output} cut={step.output_cut ?? 0} />}
          </div>
        )}
        {hasMore && !step.origin && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-ink-3 transition-colors hover:text-ink"
          >
            <Icon name={open ? "down" : "chevron"} size={11} />
            {open ? "Less" : step.output ? "Output" : step.changes?.length ? `Diff${step.changes.length > 1 ? "s" : ""}` : "More"}
          </button>
        )}
        {step.origin && step.output && (
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] font-medium text-ink-3 hover:text-ink">Output</summary>
            <Output text={step.output} cut={step.output_cut ?? 0} />
          </details>
        )}
      </div>
    </li>
  )
}

function Output({ text, cut }: { text: string; cut: number }) {
  return (
    <pre className="mt-1 max-h-72 overflow-auto rounded-lg border border-edge bg-sunken px-3 py-2 font-mono text-[11px] leading-[17px] whitespace-pre-wrap text-ink-2">
      {cut > 0 && <span className="text-ink-3">… {cut.toLocaleString()} characters earlier{"\n"}</span>}
      {text}
    </pre>
  )
}

/** An agent's message: paragraphs kept, `code` set in mono, long ones clamped until opened.
 * `marks` are words to highlight (e.g. what the prompt has in common with the change). */
export function Prose({ text, clamp, limit = 220, marks }: { text: string; clamp: boolean; limit?: number; marks?: string[] }) {
  const plain = text.replace(/\[([^\]\n]+)\]\([^)\s]+\)/g, "$1").replace(/\*\*([^*\n]+)\*\*/g, "$1")
  const shown = clamp && plain.length > limit ? plain.slice(0, limit).trimEnd() + "…" : plain
  const marker = marks?.length ? new RegExp(`(?<![\\w.])(${marks.map(escape).sort((a, b) => b.length - a.length).join("|")})(?![\\w])`, "gi") : null
  return (
    <div className="mt-0.5 text-[12.5px] leading-[19px] whitespace-pre-wrap text-ink">
      {shown.split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g).map((block, k) =>
        block.startsWith("```") ? (
          <pre key={k} className="my-1 overflow-x-auto rounded-lg bg-sunken px-2.5 py-1.5 font-mono text-[11.5px] leading-[18px]">
            {block.replace(/^```[^\n]*\n/, "").replace(/\n?```$/, "")}
          </pre>
        ) : (
          block.split(/(`[^`\n]+`)/g).map((part, i) =>
            part.startsWith("`") && part.endsWith("`") && part.length > 2 ? (
              <code key={`${k}.${i}`} className={`rounded px-1 py-px font-mono text-[11.5px] ${marks?.some((m) => m.toLowerCase() === part.slice(1, -1).toLowerCase()) ? "mark" : "bg-sunken"}`}>
                {part.slice(1, -1)}
              </code>
            ) : marker ? (
              part.split(marker).map((piece, j) =>
                j % 2 ? <mark key={`${k}.${i}.${j}`} className="mark rounded-[3px] px-0.5 text-ink">{piece}</mark> : piece,
              )
            ) : (
              part
            ),
          )
        ),
      )}
    </div>
  )
}

function escape(word: string): string {
  return word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/** A command with the session's folder written as `.`, so what it did stays readable. */
export function shorten(command: string, cwd?: string | null): string {
  const flat = command.replace(/\s+/g, " ").trim()
  if (!cwd) return flat
  return flat.split(cwd + "/").join("").split(cwd).join(".")
}
