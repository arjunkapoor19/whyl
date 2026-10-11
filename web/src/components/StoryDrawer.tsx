import { useState, type ReactNode } from "react"
import { api, useApi, useExplanations, type Brief, type Explained, type Reason, type Step, type Story } from "../api"
import { authorColor } from "../lib/colors"
import { href } from "../lib/router"
import { ago, clock, duration, exact } from "../lib/time"
import { AgentChip, Kbd, Pill, short, statusTone } from "./Chip"
import { Diff } from "./Diff"
import { Icon } from "./Icon"
import { Prose, shorten, Timeline } from "./Timeline"

type AgentStory = Extract<Story, { kind: "agent" }>

/** How a line came to be: who wrote it, what you asked, and every step around the change. */
export function StoryDrawer({ path, line, onClose }: { path: string; line: number; onClose: () => void }) {
  const { data, error, stale } = useApi<Story>(api.story(path, line))
  return (
    <aside
      className="flex h-full min-h-0 flex-col border-l border-edge bg-panel max-md:rounded-t-2xl max-md:border-t max-md:border-l-0 max-md:shadow-[var(--shadow-pop)]"
      aria-label={`Story of line ${line}`}
    >
      <div className="flex items-center gap-2 border-b border-edge px-4 py-2.5">
        <span className="mx-auto mb-1 h-1 w-9 rounded-full bg-edge-strong md:hidden" />
        <span className="font-mono text-[11.5px] text-ink-3 max-md:hidden">line {line}</span>
        <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink max-md:hidden">{data?.text ?? ""}</code>
        <span className="flex items-center gap-1 text-[11px] text-ink-3 max-md:hidden">
          <Kbd>j</Kbd>
          <Kbd>k</Kbd>
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 text-ink-3 transition-colors hover:bg-sunken hover:text-ink"
          aria-label="Close"
          title="Close (Esc)"
        >
          <Icon name="close" size={15} />
        </button>
      </div>
      <div className={`min-h-0 flex-1 overflow-y-auto px-4 py-4 transition-opacity duration-150 ${stale ? "opacity-60" : ""}`}>
        {error ? (
          <Note icon="error" title="Couldn't load this line">{error.message}</Note>
        ) : !data ? (
          <Skeleton />
        ) : (
          <StoryBody story={data} />
        )}
      </div>
    </aside>
  )
}

function StoryBody({ story }: { story: Story }) {
  const color = authorColor(story.author)
  switch (story.kind) {
    case "none":
      return (
        <Note icon="history" title="Not attributed">
          This line predates what ah has recorded or observed, or changed while nothing was watching. Run{" "}
          <code className="rounded bg-sunken px-1 font-mono">ah setup</code> so every change from now on is seen.
        </Note>
      )
    case "baseline":
      return (
        <Note icon="history" title="Pre-existing">
          Already here when ah started observing this workspace{story.author?.at ? <> ({exact(story.author.at)})</> : null}.
          No agent has changed it since.
        </Note>
      )
    case "outside":
      return (
        <div className="space-y-3">
          <Header story={story} />
          <Note icon="user" title="Edited outside any agent">
            By you or another program, between {exact(story.from)} and {exact(story.to)}.
          </Note>
          {story.changes.map((c) => <Diff key={c.path} change={c} color={color} />)}
        </div>
      )
    case "unsynced":
      return (
        <div className="space-y-3">
          <Header story={story} />
          <Note icon="clock" title="Transcript not synced yet">
            ah saw {story.tool ?? "a tool call"} change this file; the session's transcript will appear once it syncs.
          </Note>
          {story.command && <pre className="rounded-lg bg-sunken px-3 py-2 font-mono text-[11.5px] whitespace-pre-wrap">{story.command}</pre>}
          {story.changes.map((c) => <Diff key={c.path} change={c} color={color} />)}
        </div>
      )
    case "agent":
      return <AgentStoryBody story={story} color={color} />
  }
}

/** The story of an agent's line, from intent to outcome: what you asked, a summary, why the change was made
 * (as far as the record shows), the change and its check, every step of the turn, and the agent's conclusion. */
function AgentStoryBody({ story, color }: { story: AgentStory; color: string }) {
  const origin = story.steps.find((s) => s.origin)
  const check = story.steps.find((s) => s.id === story.check)
  const final = story.steps.find((s) => s.id === story.final)
  const session = story.author?.session
  const [focus, setFocus] = useState<string | null>(null)
  const show = (step: string) => {
    setFocus(step)
    requestAnimationFrame(() => document.getElementById(`step-${step}`)?.scrollIntoView({ block: "center", behavior: "smooth" }))
  }
  return (
    <div className="space-y-5" style={{ "--c": color } as React.CSSProperties}>
      <Header story={story} />
      <OtherTurns briefs={story.earlier} where="earlier" explainer={story.explainer} session={session} />
      {story.prompt && (
        <div className="animate-rise">
          <Label>You asked{story.turn ? <span className="font-normal normal-case tracking-normal"> · turn {story.turn.seq}</span> : null}</Label>
          <div className="rounded-xl rounded-tl-sm border border-edge bg-sunken/70 px-3 py-2">
            <Prose text={story.prompt} clamp={false} marks={story.highlights} />
          </div>
          {story.handoffs.map((h) => (
            <div key={h.thread} className="relative mt-2 ml-4 border-l-2 pl-3" style={{ borderColor: color }}>
              <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-ink-2">
                <Icon name="subagent" size={12} />
                handed to sub-agent <span className="font-mono">{short(h.thread)}</span>
              </div>
              {h.prompt && (
                <div className="tint rounded-lg px-3 py-1.5">
                  <Prose text={h.prompt} clamp />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {story.turn && <Summary story={story} />}
      <section>
        <Label hint="what the record shows just before the change">Why this change</Label>
        {story.reasons.length ? (
          <ul className="space-y-2">
            {story.reasons.map((r) => <ReasonRow key={r.kind} reason={r} step={story.steps.find((s) => s.id === r.step)} cwd={story.cwd} onShow={show} />)}
          </ul>
        ) : (
          <p className="text-[12px] text-ink-3">Nothing recorded before it: no note, message, thought or failing test.</p>
        )}
      </section>
      {origin && (
        <section>
          <Label hint={clock(origin.at)}>The change</Label>
          {origin.label !== "edit" && (
            <div className="mb-1.5">
              {origin.note && <div className="text-[12.5px] text-ink">{origin.note}</div>}
              <code className="line-clamp-2 font-mono text-[11px] break-words text-ink-3">$ {shorten(origin.title, story.cwd)}</code>
            </div>
          )}
          <div className="space-y-2">
            {origin.changes?.map((change) => <Diff key={change.path} change={change} color={color} />)}
          </div>
          <Checked origin={origin} check={check} />
        </section>
      )}
      <Worked story={story} color={color} focus={focus} />
      {final && !final.origin && (
        <section>
          <Label hint={clock(final.at)}>The agent concluded</Label>
          <div className="rounded-xl border border-edge px-3 py-2">
            <Prose text={final.title} clamp={false} marks={story.highlights} />
          </div>
        </section>
      )}
      <OtherTurns briefs={story.later} where="later" explainer={story.explainer} session={session} />
      {story.history.length > 1 && (
        <section>
          <Label>Line history</Label>
          <ol className="space-y-1">
            {story.history.map((h, i) => (
              <li key={i} className="flex items-center gap-2">
                <span className="w-16 shrink-0">{h.author && <AgentChip name={h.author.name} size="xs" />}</span>
                <code className={`truncate font-mono text-[11.5px] ${i === story.history.length - 1 ? "text-ink" : "text-ink-3 line-through decoration-ink-3/40"}`}>
                  {h.text}
                </code>
              </li>
            ))}
          </ol>
        </section>
      )}
      <p className="border-t border-edge pt-3 text-[11px] text-ink-3">Steps are shown in order; order is not proof of cause.</p>
    </div>
  )
}

/** The turn in plain language, written by an agent CLI when asked and kept; clearly not part of the record. */
function Summary({ story }: { story: AgentStory }) {
  const turn = story.turn!
  const explained = useExplanations([{ id: turn.id, explanation: story.explanation }])
  return <Explanation id={turn.id} explained={explained} explainer={story.explainer} status={turn.status} />
}

/** The flag every AI-generated summary carries, naming the CLI and model that wrote it. */
export function AiBadge({ by }: { by: string }) {
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-edge px-1.5 py-px text-[10px] font-medium tracking-normal text-ink-2 normal-case"
      title={`Generated by ${by} from the recorded steps; not part of the record`}
    >
      <span style={{ color: "var(--c)" }}><Icon name="spark" size={10} /></span>
      AI-generated · {by}
    </span>
  )
}

/** Asks for summaries of `turns` in one call to the explainer, only when pressed. */
export function SummarizeButton({ turns, explained, explainer, label }: {
  turns: string[]; explained: Explained; explainer: string; label: string
}) {
  const todo = explained.missing(turns)
  if (!todo.length) return null
  return (
    <button
      type="button"
      onClick={() => explained.ask(todo)}
      className="group inline-flex items-center gap-2 rounded-xl border px-3.5 py-2 text-[13px] font-semibold text-ink shadow-sm transition-[background-color,border-color,box-shadow] hover:shadow-md active:scale-[0.99] border-[color-mix(in_oklab,var(--c)_35%,var(--color-edge))] bg-[color-mix(in_oklab,var(--c)_7%,var(--color-panel))] hover:border-[color-mix(in_oklab,var(--c)_60%,var(--color-edge))] hover:bg-[color-mix(in_oklab,var(--c)_12%,var(--color-panel))]"
      title={`One call to ${explainer}; the summary is kept, so it is never paid for twice`}
    >
      <span className="transition-transform group-hover:scale-110" style={{ color: "var(--c)" }}><Icon name="spark" size={15} /></span>
      {label}
      <span className="text-[11.5px] font-normal text-ink-3">· AI, one call to {explainer}</span>
    </button>
  )
}

export function Explanation({ id, explained, explainer, status, compact = false, button = true }: {
  id: string; explained: Explained; explainer: string | null; status: string | null; compact?: boolean; button?: boolean
}) {
  const found = explained.get(id)
  const box = compact ? "rounded-lg px-2.5 py-1.5" : "rounded-xl px-3 py-2.5"
  if (found?.text) {
    return (
      <div className={`generated animate-fade-in ${box}`}>
        <div className={`flex items-center gap-1.5 text-[10.5px] font-semibold tracking-[0.08em] text-ink-3 uppercase ${compact ? "mb-0.5" : "mb-1"}`}>
          {!compact && <><span style={{ color: "var(--c)" }}><Icon name="spark" size={12} /></span> Summary</>}
          <span className={compact ? "" : "ml-auto"}><AiBadge by={found.by} /></span>
        </div>
        <Prose text={found.text} clamp={false} />
      </div>
    )
  }
  if (explained.waiting(id)) {
    return (
      <div className={`generated ${box}`} aria-busy="true">
        <div className="mb-2 flex items-center gap-1.5 text-[11px] text-ink-3">
          <span className="animate-pulse" style={{ color: "var(--c)" }}><Icon name="spark" size={12} /></span>
          Asking {explainer} for an AI-generated summary…
        </div>
        <div className="space-y-1.5">
          <div className="skeleton h-3 w-full" />
          <div className="skeleton h-3 w-[92%]" />
          {!compact && <div className="skeleton h-3 w-[70%]" />}
        </div>
      </div>
    )
  }
  const failed = found?.error ?? explained.failure
  if (failed) {
    return (
      <div className={`flex items-start gap-2 border border-dashed border-edge-strong text-[11.5px] text-ink-3 ${box}`}>
        <Icon name="spark" size={12} className="mt-0.5 shrink-0" />
        <span className="min-w-0 flex-1 break-words">Couldn't write a summary: {failed}</span>
        {explainer && (
          <button type="button" onClick={() => explained.ask([id], true)} className="shrink-0 font-medium text-ink-2 hover:text-ink">
            Try again
          </button>
        )}
      </div>
    )
  }
  if (explained.unfinished(id)) {
    return <p className="text-[11.5px] text-ink-3">This turn is still going; it can be summarized once it has finished.</p>
  }
  if (!explainer) {
    return compact ? null : (
      <p className="text-[11.5px] text-ink-3">
        Install claude, codex or opencode for an AI-generated summary of each turn.
      </p>
    )
  }
  if (!button) return null
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SummarizeButton turns={[id]} explained={explained} explainer={explainer} label="Summarize this turn" />
      {status === "incomplete" && <span className="text-[11px] text-ink-3">once it has finished</span>}
    </div>
  )
}

const REASONS: Record<Reason["kind"], { icon: string; label: string }> = {
  note: { icon: "note", label: "The agent's note on this step" },
  thought: { icon: "thinking", label: "Thinking, just before" },
  said: { icon: "agent", label: "The agent said" },
  failing: { icon: "x", label: "A test run failed just before" },
}

function ReasonRow({ reason, step, cwd, onShow }: { reason: Reason; step?: Step; cwd: string | null; onShow: (id: string) => void }) {
  const { icon, label } = REASONS[reason.kind]
  const [open, setOpen] = useState(false)
  const long = reason.text.length > 320
  return (
    <li className="animate-rise flex gap-2.5">
      <span className={`mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full ${reason.kind === "failing" ? "bg-[color-mix(in_oklab,var(--bad)_14%,transparent)] text-bad" : "tint text-ink-2"}`}>
        <Icon name={icon} size={12} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 text-[11px] text-ink-3">
          <span className="font-medium text-ink-2">{label}</span>
          <button type="button" onClick={() => onShow(reason.step)} className="ml-auto font-mono tabular-nums hover:text-ink" title="Show in the steps">
            {clock(reason.at)}
          </button>
        </div>
        {reason.kind === "failing" ? (
          <div className="mt-0.5">
            <Pill tone="bad" title={reason.text}><Icon name="x" size={11} />{reason.text}</Pill>
            {step && <code className="mt-0.5 block truncate font-mono text-[11px] text-ink-3">$ {shorten(step.title, cwd)}</code>}
          </div>
        ) : reason.kind === "note" ? (
          <div className="mt-0.5 text-[12.5px] text-ink italic">“{reason.text}”</div>
        ) : (
          <>
            <div className={reason.kind === "thought" ? "text-ink-2 [&>div]:text-ink-2" : ""}>
              <Prose text={reason.text} clamp={!open} limit={320} />
            </div>
            {long && (
              <button type="button" onClick={() => setOpen(!open)} className="mt-0.5 text-[11px] font-medium text-ink-3 hover:text-ink">
                {open ? "Less" : "All of it"}
              </button>
            )}
          </>
        )}
      </div>
    </li>
  )
}

/** How the change was checked: by the step itself, a test run after it, or not at all. */
function Checked({ origin, check }: { origin: Step; check?: Step }) {
  if (!check) {
    return (
      <div className="mt-2 flex items-center gap-1.5 text-[11.5px] text-warn">
        <Icon name="error" size={12} /> No test ran after this change in this turn.
      </div>
    )
  }
  const passed = check.status === "completed"
  const when = check === origin ? "The same step ran the tests" : `Then, ${duration(origin.at, check.at) || "moments"} later`
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11.5px] text-ink-2">
      <span>{when}:</span>
      <Pill tone={statusTone(check.status)}>
        <Icon name={passed ? "check" : "x"} size={11} />
        {check.summary ?? (passed ? "passed" : "failed")}
      </Pill>
    </div>
  )
}

/** Every step of the turn on a rail, under a one-line summary of the work; folds away. */
function Worked({ story, color, focus }: { story: AgentStory; color: string; focus: string | null }) {
  const [open, setOpen] = useState(true)
  const { work } = story
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="group mb-2 flex w-full flex-wrap items-center gap-x-2 gap-y-1 rounded-lg text-left"
        aria-expanded={open}
      >
        <Icon name={open ? "down" : "chevron"} size={12} className="text-ink-3" />
        <span className="text-[12.5px] font-semibold text-ink">
          Worked{work.took ? ` for ${duration(0, work.took)}` : ""}
        </span>
        <span className="text-[12px] text-ink-3">· {work.steps} steps</span>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          {work.files.map((f) => (
            <span key={f.path} className="inline-flex items-center gap-1 rounded-md bg-sunken px-1.5 py-px font-mono text-[10.5px]">
              {f.rel.split("/").pop()}
              {f.added > 0 && <span className="text-add">+{f.added}</span>}
              {f.removed > 0 && <span className="text-del">−{f.removed}</span>}
            </span>
          ))}
          {work.tests.length > 0 && (
            <span className="inline-flex items-center gap-0.5" title={`test runs: ${work.tests.join(", ")}`}>
              {work.tests.map((t, i) => (
                <span key={i} className={`size-2 rounded-full ${t === "passed" ? "bg-ok" : "bg-bad"}`} />
              ))}
            </span>
          )}
        </span>
      </button>
      {open && (
        <div className="animate-fade-in [&_li[id]]:rounded-lg">
          <style>{focus ? `#step-${CSS.escape(focus)} { background: color-mix(in oklab, ${color} 16%, transparent) }` : ""}</style>
          <Timeline steps={story.steps} color={color} cwd={story.cwd} originDiff={false} dense />
        </div>
      )}
    </section>
  )
}

/** The session's other turns behind a divider, with one button to summarize them all in one call. */
function OtherTurns({ briefs, where, explainer, session }: { briefs: Brief[]; where: "earlier" | "later"; explainer: string | null; session?: string | null }) {
  const [open, setOpen] = useState(false)
  const explained = useExplanations(briefs)
  if (!briefs.length) return null
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 text-[10.5px] font-semibold tracking-[0.08em] text-ink-3 uppercase hover:text-ink"
        aria-expanded={open}
      >
        <span className="h-px flex-1 bg-edge" />
        <Icon name={open ? "down" : where === "earlier" ? "up" : "down"} size={11} />
        {briefs.length} {where} {briefs.length === 1 ? "turn" : "turns"} in this session
        <span className="h-px flex-1 bg-edge" />
      </button>
      {open && explainer && (
        <div className="animate-fade-in mt-2">
          <SummarizeButton turns={briefs.map((b) => b.id)} explained={explained} explainer={explainer}
            label={briefs.length === 1 ? "Summarize this turn" : `Summarize these ${briefs.length} turns`} />
        </div>
      )}
      {open && (
        <ol className="animate-fade-in mt-2 space-y-2">
          {briefs.map((b) => (
            <li key={b.id} className="rounded-xl border border-edge px-3 py-2">
              <div className="flex items-baseline gap-2 text-[11px] text-ink-3">
                <span className="font-semibold text-ink-2">Turn {b.seq}</span>
                {b.status && b.status !== "completed" && <Pill tone={statusTone(b.status)}>{b.status}</Pill>}
                <span>{b.steps} steps</span>
                {b.files.length > 0 && <span className="truncate font-mono">{b.files.map((f) => f.split("/").pop()).join(", ")}</span>}
                <span className="ml-auto shrink-0 font-mono tabular-nums" title={exact(b.started_at)}>{clock(b.started_at)}</span>
              </div>
              {b.prompt && <div className="mt-1 line-clamp-2 text-[12.5px] text-ink">{b.prompt}</div>}
              <div className="mt-1.5">
                <Explanation id={b.id} explained={explained} explainer={explainer} status={b.status} compact button={false} />
              </div>
              {session && (
                <a href={href({ name: "session", id: session })} className="mt-1 inline-flex items-center gap-1 text-[11px] text-ink-3 hover:text-ink">
                  Open the session <Icon name="arrow" size={10} />
                </a>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function Header({ story }: { story: Story }) {
  const author = story.author
  if (!author) return null
  const agentStory = story.kind === "agent" ? story : null
  return (
    <div className="animate-rise flex flex-wrap items-center gap-x-2 gap-y-1">
      <AgentChip name={author.name} size="md" title={author.agent ?? undefined} />
      {agentStory?.agent_version && <span className="text-[11px] text-ink-3">{agentStory.agent_version}</span>}
      {author.session && (
        <a
          href={href({ name: "session", id: author.session })}
          className="font-mono text-[11.5px] text-ink-2 underline decoration-edge-strong underline-offset-2 hover:text-ink"
          title={author.session}
        >
          {short(author.session, 10)}
        </a>
      )}
      {author.turn != null && <span className="text-[11.5px] text-ink-2">turn {author.turn}</span>}
      {agentStory?.handoffs.map((h) => (
        <span key={h.thread} className="flex items-center gap-1 text-[11.5px] text-ink-2">
          <Icon name="chevron" size={11} className="text-ink-3" />
          sub-agent <span className="font-mono">{short(h.thread)}</span>
        </span>
      ))}
      <span className="ml-auto text-[11.5px] text-ink-3" title={exact(author.at)}>
        {ago(author.at)}
      </span>
      {(agentStory?.observed || author.concurrent) && (
        <div className="flex w-full gap-1.5">
          {agentStory?.observed && (
            <span className="inline-flex items-center gap-1 rounded-md bg-sunken px-1.5 py-px text-[10.5px] text-ink-2" title="ah saw the file change around this tool call">
              <Icon name="eye" size={11} /> observed by ah
            </span>
          )}
          {author.concurrent && (
            <span className="inline-flex items-center gap-1 rounded-md bg-[color-mix(in_oklab,var(--warn)_14%,transparent)] px-1.5 py-px text-[10.5px] text-warn">
              ≈ another tool call ran at the same time; the line may be from either
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function Label({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <div className="mb-1.5 flex items-baseline gap-2 text-[10.5px] font-semibold tracking-[0.08em] text-ink-3 uppercase">
      <span>{children}</span>
      {hint && <span className="font-normal tracking-normal normal-case">{hint}</span>}
    </div>
  )
}

function Note({ icon, title, children }: { icon: string; title: string; children: ReactNode }) {
  return (
    <div className="animate-rise flex gap-3 rounded-xl border border-edge bg-sunken/50 p-3">
      <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-panel text-ink-2 ring-1 ring-edge">
        <Icon name={icon} size={14} />
      </span>
      <div className="min-w-0 text-[12.5px] leading-[19px] text-ink-2">
        <div className="mb-0.5 font-semibold text-ink">{title}</div>
        {children}
      </div>
    </div>
  )
}

function Skeleton() {
  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <div className="skeleton h-6 w-20 rounded-full" />
        <div className="skeleton h-6 w-32" />
      </div>
      <div className="skeleton h-16 w-full rounded-xl" />
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex gap-3">
          <div className="skeleton size-7 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <div className="skeleton h-3 w-24" />
            <div className="skeleton h-3.5 w-full" />
          </div>
        </div>
      ))}
    </div>
  )
}
