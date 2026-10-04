# 001 — Notes

Scratchpad for decisions, open questions, and anything surprising. Keep it chronological.

## Open questions (resolve before `Status: approved`)

- **`Method —calls→ Method` scope.** Call graph relations can explode. Decide whether the first ship records them at all, or whether agents start by only emitting `defines`.
- **`removeField` on required columns.** SQLite 3.35+ supports `DROP COLUMN` but not for columns referenced by indexes/triggers. The FTS trigger teardown story is covered in task 14; verify it holds for every seeded inventory.

## Decisions log

- 2026-10-04 — Project initialized under spec-driven development. Chose Python + FastMCP over Rust for iteration speed, keeping Rust as a future port option.
- 2026-10-04 — Chose SQLite BLOBs + Python cosine over `sqlite-vec` for the first implementation. Revisit only if measurements demand it.
- 2026-10-04 — No auth in the MCP server. agentgateway handles TLS, access control, rate limiting, and public read-only exposure.
- 2026-10-04 — Embeddings run CPU-only on the NAS (Ryzen 8600G); the 780M iGPU is not used. Container image carries no GPU runtime.
- 2026-10-04 — agentgateway endpoint fixed: `gateway.bizrader.uk` → `192.168.178.208`.
- 2026-10-04 — Pivoted to a typed-graph model (inventories, nodes, edges) with two tools: `get_metamodel` and `graphql`. Metamodel is dynamic; one SQLite table per inventory; DDL runs at runtime. SOP is one inventory with a `category` enum. Every node has one embedding over `name + description`.
- 2026-10-04 — Clarified SOP vs. execution reports. SOP documents include `running` SOPs. Execution reports are a separate inventory, renamed `RunResult`.
- 2026-10-04 — Dropped `RunResult` from the built-in metamodel. Per-execution reports would bloat the server without durable value; agents keep run output local. Durable execution knowledge belongs in a `running` SOP.
- 2026-10-04 — Added users and attribution: `users` is a dedicated table (not an inventory), seeded with one `admin`. Every node carries `owned_by` and `updated_by` as base columns. Caller identity arrives as `X-Actor-Id`; absent or unknown falls back to `admin`. No cryptographic verification — agentgateway's problem later.
- 2026-10-04 — Admin UI added: React + Vite SPA baked into the server image, served at `/ui`, full CRUD on nodes/edges/metamodel/users through the shared GraphQL schema.
- 2026-10-04 — Spec 001 moved to `in-progress`. GraphQL library: `strawberry` (code-first, decorators, type hints; easier to rebuild schema at runtime). UI GraphQL client: `urql` (normalized cache, hooks).
- 2026-10-04 — Implementation decisions through task 11:
  - Inventory tables are named `inv_<snake_case>` so they are trivially recognisable in a backup; mapping lives in `db.metamodel.table_name_for`.
  - `node_fts` is a single FTS5 virtual table `(inventory, node_id UNINDEXED, text)` rather than one per inventory. Tokenization lives on the writer side (not implemented yet) and the schema uses `unicode61 remove_diacritics 2` as the baseline tokenizer.
  - Node ids are UUID4 hex (`new_id()`); `admin` user is the one exception, keeping the literal id `admin`.
  - Attribution columns `owned_by` / `updated_by` are `NOT NULL REFERENCES users(id)` on every inventory table. Enforced via `PRAGMA foreign_keys = ON`.
  - Startup is "connect() then initialize()"; both are idempotent. The relation seeding step always runs so adding a built-in relation later does not need a migration.
- 2026-10-04 — fastembed confirmed CPU-only: `onnxruntime` wheel (not `onnxruntime-gpu`); available providers are `CPUExecutionProvider` and the no-op `AzureExecutionProvider`. Default model is `BAAI/bge-small-en-v1.5`, 384 dims. Lazy-initialised to keep tests fast.
- 2026-10-04 — Vectors are stored as little-endian float32 blobs via `struct.pack`. Decoded on read; the brute-force cosine scan happens in Python at query time.
- 2026-10-04 — Metamodel ops moved to `db/metamodel_ops.py`; `db/metamodel.py` stays as a thin storage helper used both by seeding and by the ops layer.
- 2026-10-04 — Pivoted GraphQL implementation from `strawberry-graphql` to raw `graphql-core`. Strawberry's type system assumes classes are declared at import time; our schema is fully dynamic (new inventories can appear at runtime). `graphql-core` lets us assemble `GraphQLObjectType` instances directly from the metamodel. `plan.md` updated to match. `strawberry-graphql` stays listed as a transitive dependency for now but is unused.
- 2026-10-04 — Task 24 landed with base fields, declared fields, and the per-inventory lookup/list queries. **Relation traversal fields on node types are deferred to task 26** (data mutations), because designing them cleanly requires having the edge-write operations in the same change. Flagged for human review.
- 2026-10-04 — Task 26 landed: `createX`/`updateX`/`deleteX` per inventory, generic `connect(relation, fromId, toId)` / `disconnect(...)`, and relation traversal fields on node types (outgoing = `camelCase(relation)`, incoming = `camelCase(relation) + "In"`). Storage split across `node_ops.py` (node CRUD) and `edge_ops.py` (edges). Edges reuse the minimal `(from_id, relation, to_id)` schema from task 2; relation cardinality is not currently enforced at the edge layer — relations are declared with `cardinality="many"` and the field is informational only. Flagged.
- 2026-10-04 — The fake embedder in `tests/test_embeddings.py` was previously producing NaN/inf float32 values by reinterpreting raw SHA bytes. Tests that used it worked by luck; adding more call sites in task 26 made the flakiness visible. Replaced with a byte→`[-1, 1]` mapping that keeps cosine well-behaved. Not a production change.
