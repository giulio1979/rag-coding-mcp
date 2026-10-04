# AGENTS.md

Operating manual for AI coding agents working on this repository. Humans may read it too.

This project follows **spec-driven development**: no non-trivial code lands without a spec it can be traced to.

---

## 1. Project in one paragraph

A single, central **knowledge-only MCP server** running on a NAS (k3s). Editors on laptops connect to it; phones can read through a public read-only endpoint. The server stores repository overviews, file summaries, symbols, SOPs (spec/plan/test/run), plan items, audits and runs in SQLite. **It never sees source code.** Agents run builds and tests locally and report structured results back through MCP tools. Semantic search is a hybrid of SQLite FTS5 and vector similarity over embeddings produced server-side.

Full design intent lives in `specs/001-core-mcp-server/spec.md`. That spec, not this file, is the source of truth for product behavior.

---

## 2. Stack

- **Language:** Python 3.11+
- **MCP framework:** FastMCP
- **Storage:** SQLite (single file on a PVC in production)
- **Search:** SQLite FTS5 (built-in) + vector similarity over embeddings
  - Start with vectors stored as BLOBs and cosine computed in Python. Only introduce `sqlite-vec` if measurements demand it.
- **Embeddings:** computed server-side, model name + dimension + source text stored alongside the vector so re-embedding is cheap.
- **Packaging:** container image, glibc base (Debian slim), not Alpine.

If a spec requires deviating from this stack, the deviation goes in that spec's `plan.md` with a short justification. Do not quietly swap libraries.

---

## 3. Repository layout

```
AGENTS.md                      ← this file
README.md                      ← short human-facing intro
specs/
  NNN-slug/
    spec.md                    ← what and why (required)
    plan.md                    ← how, at a design level (required before code)
    tasks.md                   ← ordered, checkable task list (required before code)
    notes.md                   ← optional scratchpad, decisions, open questions
src/                           ← implementation (created when first spec reaches implementation)
tests/                         ← tests (created alongside src)
```

Rules:

- Spec folders are numbered with a zero-padded three-digit prefix (`001-`, `002-`, …) and a short kebab-case slug.
- Numbers are allocated in order and never reused, even if a spec is abandoned.
- An abandoned spec stays on disk with a `Status: abandoned` line at the top of its `spec.md` and a one-line reason.

---

## 4. The workflow

Three phases per feature: **spec → implementation → tests**. Audit is a lightweight check after tests pass, not a separate phase.

### 4.1 Spec phase

Deliverables, in this order, in `specs/NNN-slug/`:

1. **`spec.md`** — problem, scope, non-goals, user-visible behavior, data shapes, acceptance criteria. No implementation detail.
2. **`plan.md`** — chosen approach, key design decisions, data/schema changes, risks, alternatives rejected. Short.
3. **`tasks.md`** — ordered, atomic, checkable tasks that collectively satisfy `spec.md`. Each task is a single bullet starting with `- [ ]` and small enough to review in one sitting.

An agent may draft all three in one pass, but must not start writing code in `src/` until a human has reviewed and merged (or explicitly approved) `spec.md`, `plan.md`, and `tasks.md`.

Required front matter at the top of every `spec.md`:

```markdown
# NNN — <Title>

- Status: draft | approved | in-progress | shipped | abandoned
- Owner: <name or agent>
- Updated: YYYY-MM-DD
```

### 4.2 Implementation phase

- Work tasks in the order listed in `tasks.md`. Check them off as you go.
- Every commit message (or PR title) references the spec: `001: add repos table`.
- If reality diverges from `plan.md`, update `plan.md` in the same change. Plans are living documents until the spec ships; they are not retroactively rewritten after that.
- New dependencies: add to `pyproject.toml` (or equivalent) **and** mention in `plan.md`.

### 4.3 Tests phase

- Every acceptance criterion in `spec.md` has at least one automated test.
- Tests live in `tests/`, mirroring `src/` structure.
- Agents run the test suite locally (or in CI) and report results. The MCP server itself never runs tests on behalf of agents — this is by design (see spec 001).
- A spec is **shipped** only when: all tasks are checked, all acceptance criteria have passing tests, and `spec.md` status is updated to `shipped`.

### 4.4 Audit (lightweight)

After tests pass, before marking a spec shipped, do a one-pass check:

- Does the implementation match `spec.md` as written, not as remembered?
- Are there behaviors in the code that aren't in the spec? If yes, either remove them or add them to the spec.
- Are there acceptance criteria without a corresponding test?

Record anything surprising in `notes.md`.

---

## 5. Rules for AI agents

These are non-negotiable unless a human explicitly overrides them in-conversation.

1. **Spec before code.** If asked to implement something that has no spec, write the spec first and stop for review. Do not write production code speculatively.
2. **One spec, one concern.** Resist bundling unrelated changes into one spec. If scope grows mid-flight, split into a new numbered spec.
3. **No silent dependencies.** New runtime or dev dependencies are called out in `plan.md`.
4. **Keep diffs minimal.** Don't refactor code you weren't asked to touch. Don't add docstrings, type annotations, or comments to code outside your task's scope.
5. **No invented facts.** If a design decision isn't in `spec.md` or `plan.md`, surface the question in `notes.md` or ask. Don't guess and ship.
6. **The server never sees code.** Any proposal that routes source code through the MCP server violates spec 001 and must be rejected or escalated.
7. **Test what the spec promises, not what the code happens to do.** Tests are derived from acceptance criteria.
8. **Status discipline.** Update `Status:` and `Updated:` in `spec.md` when you change phase. Stale status is a bug.

---

## 6. Conventions

- **Markdown:** one H1 per file; ATX headings; wrap at ~100 chars when it doesn't hurt readability.
- **File names:** kebab-case for docs, snake_case for Python modules.
- **Dates:** ISO 8601 (`YYYY-MM-DD`).
- **Commits:** `NNN: short imperative summary` where `NNN` is the spec number. Chore commits with no spec use `chore: …`.

---

## 7. Current specs

| #   | Slug              | Status      | Summary                                       |
| --- | ----------------- | ----------- | --------------------------------------------- |
| 001 | core-mcp-server   | in-progress | Central knowledge-only MCP server on the NAS. |

Add a row here whenever a new spec folder is created.
