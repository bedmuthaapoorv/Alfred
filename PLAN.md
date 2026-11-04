# Alfred — a proactive, self-improving Claude agent

## 1. What Alfred is

Most assistants wait to be asked. Alfred runs all the time. It watches what is
happening in your digital life, decides what matters, does the safe things on
its own, asks before doing the risky ones, and gets better at all of this by
studying how its past decisions turned out.

Three properties define it:

| Property | Meaning in practice |
|---|---|
| **Proactive** | Wakes up on events (new mail, upcoming meeting, failing CI) and on a heartbeat, without a prompt from you. |
| **Autonomous, but bounded** | Acts alone inside an explicit policy; anything outward-facing or irreversible goes through you until it has earned trust. |
| **Self-improving** | Measures its own outcomes, reflects, and proposes (and, within limits, applies) changes to its own prompts, playbooks and code. |

---

## 2. Architecture

```
            ┌────────────────────────── Sensors ───────────────────────────┐
            │ Gmail · Calendar · Drive · GitHub · webhooks · cron heartbeat│
            └───────────────────────────────┬──────────────────────────────┘
                                            │ normalized Events
                                            ▼
┌──────────────┐   ┌───────────────┐   ┌────────────┐   ┌───────────────────┐
│ Memory       │◄─►│ Triage        │──►│ Planner    │──►│ Policy gate       │
│ (profile,    │   │ cheap model:  │   │ strong     │   │ autonomy tier per │
│  episodes,   │   │ ignore / note │   │ model +    │   │ action → auto /   │
│  playbooks)  │   │ / act / ask   │   │ tools      │   │ ask / forbid      │
└──────▲───────┘   └───────────────┘   └────────────┘   └─────────┬─────────┘
       │                                                          │
       │            ┌────────────── Executor ──────────────┐      │
       │            │ MCP tools (mail, calendar, docs, gh) │◄─────┘
       │            │ every action logged + reversible     │
       │            └──────────────────┬───────────────────┘
       │                               ▼
       │            ┌────────────── Outcome log ───────────┐
       └────────────│ approved / edited / rejected / undone│
                    │ / ignored, latency, cost             │
                    └──────────────────┬───────────────────┘
                                       ▼
                    ┌──────── Reflection & Self-improvement ────────┐
                    │ nightly review → hypotheses → eval → change   │
                    └───────────────────────────────────────────────┘
```

### 2.1 Runtime
- **Claude Agent SDK (Python)** as the agent loop; a long-running daemon
  (`alfred serve`) with an asyncio event bus.
- **Model routing:** Haiku for triage and summarization (high volume, cheap);
  Opus for planning, reflection and self-modification; Sonnet as the middle
  tier for routine drafting. Model IDs live in config, not code.
- **Tools via MCP:** Gmail, Google Calendar, Drive/Docs, GitHub, plus a local
  shell/filesystem sandbox. New capabilities are added as MCP servers, so
  Alfred can propose new tools without touching the core.

### 2.2 Sensors (what makes it proactive)
| Trigger | Examples |
|---|---|
| **Push events** | New email, calendar invite, PR review requested, CI failed, webhook. |
| **Heartbeat** (every N min) | "Anything due soon? Anything stale? Anything I promised to follow up on?" |
| **Scheduled routines** | Morning brief, end-of-day wrap-up, weekly review. |
| **Self-scheduled follow-ups** | Alfred sets its own reminders: "check whether Sam replied by Thursday". |
| **Anticipation** | Before each meeting: gather the docs, last thread, open questions. |

Every trigger becomes an `Event {source, kind, payload, received_at}`.

### 2.3 Triage → Plan → Act
1. **Triage** (cheap model) classifies each event as `ignore | remember |
   act | ask` and scores urgency. Most events stop here.
2. **Planner** (strong model) gets the event, relevant memory and matching
   playbooks, and produces a plan: a list of typed `Action`s, each with a
   rationale and a confidence.
3. **Policy gate** checks each action against the autonomy policy (below).
4. **Executor** runs allowed actions, queues the rest as approval requests
   (push notification / daily digest / chat), and logs everything.

### 2.4 Autonomy policy
Actions are classified into tiers. The policy file is **read-only to Alfred**.

| Tier | Behaviour | Examples |
|---|---|---|
| **T0 — Observe** | Always allowed | Read mail, read calendar, summarize, take notes. |
| **T1 — Private & reversible** | Auto | Draft replies (not send), label/archive mail, create personal reminders, prep docs, open draft PRs on its own repo. |
| **T2 — Outward-facing** | Ask, unless promoted | Send email, accept/decline invites, comment on GitHub, share a file. |
| **T3 — Forbidden** | Never | Payments, deleting data permanently, changing security settings, editing its own policy or guardrails. |

**Trust ladder:** a specific T2 action pattern (e.g. "decline recruiter
emails with my standard note") can be promoted to auto after N consecutive
approvals with no edits, *and only with your one-time confirmation*. Any
rejection or undo demotes it again. Every auto action has an undo window
where possible (e.g. delayed send).

### 2.5 Memory
| Kind | Content | Store |
|---|---|---|
| **Profile** (semantic) | Preferences, people, projects, tone, working hours | Markdown files + SQLite |
| **Episodic** | Every event, plan, action and outcome | SQLite (append-only) |
| **Procedural** | Playbooks: "how I handle X", with trigger, steps, examples | `playbooks/*.md`, versioned in git |
| **Commitments** | Things you or Alfred promised, with due dates | SQLite, polled by heartbeat |

Retrieval: recency + keyword + embeddings over episodes; playbooks matched by
trigger description. The profile is human-readable and editable, so you can
see and correct what Alfred believes about you.

---

## 3. Self-improvement loop

This is what turns Alfred from a bot into something that compounds.

### 3.1 Signals it learns from
- **Explicit:** approved / rejected / edited (with the diff) / undone.
- **Implicit:** drafts you ignored, briefs you never opened, notifications
  you dismissed, how long things waited for approval.
- **Operational:** errors, tool failures, cost and latency per task.
- **Direct feedback:** "Alfred, stop doing X" or a thumbs-down with a reason.

### 3.2 The nightly reflection
A scheduled Opus run that:
1. Reads the day's outcome log and computes metrics (approval rate, edit
   distance on drafts, false-alarm rate, missed items you handled yourself).
2. Clusters failures and writes **hypotheses**: "My replies to X are too
   formal — 4/5 got edited to be shorter."
3. Turns each hypothesis into a **proposed change** at one of these levels:

| Level | What changes | How it's applied |
|---|---|---|
| **Memory** | Profile facts, preferences | Auto, logged, shown in the weekly review. |
| **Playbooks & prompts** | `playbooks/`, prompt templates, triage rules | Auto **if** the eval suite passes and score ≥ baseline; versioned, one-command rollback. |
| **Tools & code** | New MCP server, bug fix, new sensor | Opened as a **pull request** on this repo; you merge. |
| **Policy & guardrails** | Autonomy tiers, forbidden list | **Never** self-applied. Alfred can only *suggest* in the weekly review. |

4. Identifies **capability gaps**: tasks you did by hand that Alfred could have
   done, and proposes new playbooks or tools for them ("You reschedule
   1:1s every Monday by hand. I could do that. Here's a playbook draft").

### 3.3 Evals: the guardrail on self-modification
- `evals/` holds replayable cases built from real episodes (with PII
  scrubbed): input event + memory snapshot → expected decision/action.
- Every rejected or edited action becomes a new eval case automatically.
- A proposed change must not lower the overall score or break any
  "golden" case. Scores per version are stored, so regressions are visible.
- LLM-as-judge for fuzzy outputs (draft quality), exact checks for
  structured ones (triage label, action tier).

### 3.4 Weekly review (human in the loop)
A short artifact: what Alfred did, what it learned, changes it applied
(with rollback links), PRs awaiting you, trust-ladder promotions it wants,
and the one or two things it thinks would help most next.

---

## 4. Safety and control
- **Kill switch:** `alfred pause` stops all T1+ actions instantly.
- **Full audit log** of every action with its rationale.
- **Prompt-injection defence:** content from email/web/docs is data, never
  instructions; planner prompts mark it as untrusted, and any action derived
  from external content needs a higher confidence or gets bumped one tier.
- **Rate limits & budgets:** max actions per hour, daily token/cost budget;
  exceeding either drops Alfred to observe-only and notifies you.
- **Self-modification boundary:** Alfred's write access is limited to
  `memory/`, `playbooks/`, `prompts/` and branches in its own repo. `policy/`
  and `core/guardrails` are outside it at the filesystem level, not just by
  instruction.
- **Secrets** stay in env/secret manager, never in memory or logs.

---

## 5. Repository layout

```
alfred/
  core/          event bus, daemon, scheduler, model router
  sensors/       gmail.py, calendar.py, github.py, heartbeat.py, webhooks.py
  agent/         triage.py, planner.py, executor.py
  policy/        tiers.yaml, guardrails.py        (read-only to Alfred)
  memory/        store.py, retrieval.py, profile/*.md
  playbooks/     *.md                               (self-editable)
  prompts/       *.md                               (self-editable)
  reflection/    nightly.py, hypotheses.py, proposer.py
  evals/         cases/, runner.py, judge.py
  interface/     cli.py, notifications.py, digest.py, approvals.py
tests/
PLAN.md
```

---

## 6. Roadmap

| Phase | Goal | Done when |
|---|---|---|
| **0. Skeleton** (week 1) ✅ | Daemon, event bus, SQLite log, CLI, config, model router | `alfred serve` runs and logs heartbeat events. |
| **1. Observer** (weeks 2–3) | Gmail + Calendar sensors, triage, morning brief | Daily brief you actually read; triage accuracy measured against your labels. |
| **2. Assistant** (weeks 4–5) | Planner, executor, policy gate, approval queue, T1 actions | Alfred drafts replies and preps meetings unprompted; you approve T2 from your phone. |
| **3. Memory** (week 6) | Profile, commitments, playbooks, retrieval | Alfred follows up on commitments without being reminded. |
| **4. Learner** (weeks 7–8) | Outcome logging, eval suite, nightly reflection, auto-applied playbook/prompt changes with rollback | First self-applied improvement that measurably raises approval rate. |
| **5. Builder** (weeks 9–10) | Alfred opens PRs on its own repo for tools/code; capability-gap detection; trust ladder | Alfred ships a new playbook or tool you merged, based on something it noticed you doing by hand. |
| **6. Expand** | GitHub, Drive, Slack, more sensors | Driven by Alfred's own capability-gap proposals. |

### Metrics to track from day one
- Approval rate and edit distance of proposed actions
- Undo rate of auto actions (must stay near 0)
- Time saved (estimated per action type)
- Items you handled yourself that Alfred should have caught
- Cost per day

---

## 7. Decisions
- **Runs on:** an always-on server (systemd user service).
- **First domains:** email + calendar.

## 8. Open questions
1. **How do you want to be reached** for approvals: push notification,
   email digest, Telegram/Slack, or a small web dashboard?
2. **Risk appetite:** comfortable with Alfred sending email on its own after
   promotion, or should sending always stay T2?
3. **Self-applied changes:** are you OK with auto-applied prompt/playbook
   changes (eval-gated, with rollback), or should every change be a PR at
   first?
