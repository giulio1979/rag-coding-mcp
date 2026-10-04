# 001 — Plan

Design-level choices for the core MCP server. Living document until the spec ships.

- Updated: 2026-10-04

## Language and framework

- **Python 3.11+ with FastMCP.** Chosen for iteration speed. The metamodel, GraphQL schema and SQLite schema are language-agnostic; a port to Rust remains possible later if operational concerns justify it.
- **GraphQL schema:** built directly on `graphql-core` (strawberry's own dependency) because the schema is rebuilt from the metamodel at runtime, and `graphql-core`'s object-model constructors fit that use case more honestly than strawberry's decorators. `strawberry-graphql` stays in deps for serving infrastructure where it is cleaner.
- Dependency management: `pyproject.toml` with `uv`.

## Transport

- FastMCP over **streamable HTTP**, one route exposing exactly two tools: `get_metamodel` and `graphql`.
- The same server also serves the admin UI's static assets under `/ui` and (optionally) a `/graphql` HTTP endpoint for the SPA to call directly without going through MCP. The GraphQL schema and resolvers are shared between the MCP tool and the HTTP endpoint; neither is privileged over the other.
- **No auth in the server.** [agentgateway](https://agentgateway.dev/) runs in front and owns TLS, access control, and rate limiting.
- **Actor identity:** every HTTP request may carry an `X-Actor-Id` header naming an existing user. The server reads it, resolves it against the `users` table, and threads the resulting user id into write paths. If the header is absent or references an unknown id, writes are attributed to the seeded `admin` user. No cryptographic verification — that is agentgateway's job (and today we have none).
- **Gateway endpoint:** `gateway.bizrader.uk` → NAS LAN `192.168.178.208`. All clients hit the gateway hostname; the gateway routes to this server in-cluster.
- The server assumes any caller is authorized and must not be exposed directly to the internet.

## Storage

- Single **SQLite** file on a PVC. WAL mode. One writer, many readers is fine at this scale.
- **One table per inventory.** The schema is dynamic: `createInventory` runs a `CREATE TABLE`, `addField` runs `ALTER TABLE ADD COLUMN`, `removeField` runs the SQLite 3.35+ `ALTER TABLE DROP COLUMN`. Metamodel changes are DDL and happen inside a transaction with the metamodel row update.
- Fixed support tables:
  - `users(id PK, name UNIQUE, is_admin BOOL, created_at)` — outside the metamodel, not exposed through generic graph operations.
  - `metamodel_inventories(name PK, description, created_at)`
  - `metamodel_fields(inventory, name, type, enum_values_json, required, description, PK(inventory, name))`
  - `metamodel_relations(name PK, from_inventory, to_inventory, cardinality, description)`
  - `edges(from_id, relation, to_id, PK(from_id, relation, to_id))` — one table for all edges, keyed by relation name. Relation name is validated against `metamodel_relations` on insert.
  - `embeddings(inventory, node_id, model, dim, vector BLOB, source_text, PK(inventory, node_id))` — one row per node (one embedding per node). The `model` column records which model produced it.
  - `node_fts(inventory, node_id, text, …)` — FTS5 virtual table mirroring `name + description + text fields` for every node.
- Every inventory table carries the base columns: `id PK`, `name`, `description`, `owned_by`, `updated_by`, `created_at`, `updated_at`. `owned_by` and `updated_by` are foreign keys to `users.id`. Inventory-specific fields are added as columns alongside.
- At startup the server reads `metamodel_*` and either seeds the built-in metamodel + the `admin` user (fresh DB) or rebuilds the in-memory GraphQL schema from it.

## GraphQL schema generation

- **Code-first, built at startup and after every successful metamodel mutation.** Every inventory becomes a GraphQL type with its base fields plus its declared fields.
- For every inventory `X`, the schema exposes:
  - `x(id: ID!): X` — single lookup.
  - `xs(filter: XFilter, text: String, similarTo: String, limit: Int, offset: Int): [XResult!]!` — list with filters; `XResult` wraps the node plus a `matchedBy` enum.
  - Relation traversal fields on `X` (one per outgoing relation declared in the metamodel).
- Data mutations: `createX`, `updateX`, `deleteX`, plus generic `connect(fromId, relation, toId)` and `disconnect(…)`. Create and update resolvers read the actor id from the request context and set `owned_by` / `updated_by` accordingly.
- Metamodel mutations: `createInventory`, `addField`, `removeField`, `createRelationType`, `removeRelationType`. These acquire a short exclusive lock, do DDL + metamodel row updates in one transaction, then trigger a schema rebuild.
- User management: a small fixed slice of the schema — `users` query, `createUser(name, isAdmin)`, `renameUser(id, name)`, `setUserAdmin(id, isAdmin)` — reading and writing the `users` table. `User` is a GraphQL type but is not an inventory; it has no embedding, no FTS entry, and no entry in the metamodel document.
- Schema rebuild is in-process and takes effect for subsequent calls. In-flight queries continue against the old schema; this is acceptable at single-user scale.

## Search

- **Lexical:** FTS5. Identifier splitting on camelCase and snake_case is applied as a preprocessing step before insert into `node_fts`.
- **Semantic:** store embeddings as BLOBs in `embeddings`; cosine computed in Python over the rows pre-filtered by inventory. At the expected scale (low thousands of vectors at 384 dims) this is milliseconds.
- **Fusion:** reciprocal rank fusion with a fixed `k` (default 60).
- `sqlite-vec` is explicitly not adopted now; revisit only if the brute-force scan hurts.

## Embeddings

- Computed server-side on write and on query (for `similarTo`).
- **CPU only.** Target hardware is a Ryzen 8600G (Zen 4, AVX2/AVX-512 available) with a Radeon 780M iGPU; the iGPU is not used. No ROCm, no CUDA, no GPU runtime in the container.
- Default model: a small 384-dim English model (BGE-small-en-v1.5 or equivalent). Model is a config value, not hardcoded.
- Every node has exactly one embedding row, keyed by `(inventory, node_id)`. The `model` column records which model produced it. On model change, rows with a stale `model` are flagged and lazily re-embedded or triggered by an admin command.
- Embedding source is always `name + "\n" + description`. Field-level embeddings are out of scope.

## Admin UI

- **Stack:** React + Vite, TypeScript, a GraphQL client (`urql` or `graphql-request`), and a small routing library. No server-side rendering.
- **Packaging:** the SPA lives in `ui/` at the repo root, has its own `package.json`. The Dockerfile builds it in a Node builder stage, then copies the static output into the Python image. The Python server mounts the output directory at `/ui` and serves `index.html` for any unknown subpath to support client-side routing.
- **Transport to the server:** the SPA calls a plain `/graphql` HTTP endpoint on the same host (same schema as the MCP tool). Every outgoing request includes an `X-Actor-Id` header read from a UI-local setting (defaulted to `admin`).
- **Metamodel reactivity:** the SPA refetches `get_metamodel`-equivalent data after every metamodel mutation and after a manual refresh action; dynamic forms are generated from the current metamodel.
- **Scope:** full CRUD on nodes, edges, metamodel entries and users. Destructive actions have a two-step confirmation and surface the server's structured errors verbatim.
- **Not in scope:** auth, theming, i18n, offline support, mobile layout polish.

- Container image on a **glibc base** (Debian slim), non-root user, writable `/data` for the SQLite file. Multi-stage build: Node stage for the SPA, Python stage for the server, final stage copies both into the runtime image.
- k3s: Deployment + PVC + Service + NetworkPolicy restricting ingress to the agentgateway pod.
- agentgateway configuration (external to this repo but documented here) exposes routes against the same backend: an editor route (full MCP + GraphQL + `/ui`), a phone route (queries only; mutations rejected at the gateway; `/ui` optional).
- Backups: periodic `sqlite3 .backup` to a secondary PVC or Nextcloud mount.

## Risks

- **Runtime DDL correctness.** Dynamic schema evolution is the sharpest edge. Mitigation: every metamodel mutation is a single transaction covering DDL + metamodel rows + FTS table rebuild if needed; integration tests cover each mutation type including failure modes (duplicate name, orphaned data on remove).
- **FTS5 tokenization for identifiers.** Default tokenizer won't split camelCase/snake_case well. Mitigation: preprocess tokens before `INSERT INTO node_fts`.
- **Embedding staleness on model change.** Mitigation: `embeddings` table carries `model` and `dim`; `get_metamodel` reports the current model so agents and operators can detect divergence; an admin command re-embeds in bulk.
- **GraphQL schema rebuild races.** Mitigation: serialize metamodel mutations through a single writer; rebuilds are cheap enough to redo on each one.
- **Direct exposure of the server.** No auth in-process. Mitigation: NetworkPolicy restricts ingress to the agentgateway pod only.
- **Spoofed `X-Actor-Id`.** The server trusts the header. Mitigation: documented as a non-goal; agentgateway becomes the enforcement point when real auth lands.
- **UI drift from server metamodel.** Dynamic forms depend on `get_metamodel` output. Mitigation: UI refetches after every metamodel mutation and on manual refresh; e2e test asserts a newly created inventory appears in the UI without a reload bypass.

## Rejected alternatives

- **Many narrow tools (previous draft).** Replaced by two tools over GraphQL. GraphQL already expresses retrieval, writes and schema introspection; eleven bespoke tools duplicated that work.
- **Generic nodes/edges + JSON fields.** Would avoid runtime DDL but push typing out of SQLite and into application code, make FTS and relational joins awkward, and break the "one table per inventory" clarity we want for inspection and backups.
- **`User` as an inventory.** Would make attribution clean (edges), but every node would get a user embedding and FTS entry it doesn't need, and the admin UI would have to special-case the User inventory anyway. Kept as a dedicated table.
- **Attribution as edges.** Writing two edges on every node update doubles write cost and complicates list queries; columns are faster and sufficient.
- **SSR admin UI (htmx/jinja).** Smaller footprint but ties the UI's shape to the server's templating. A thin SPA against the shared GraphQL schema makes the UI reusable and keeps the server focused on data.
- **Rust (rmcp) now.** Reasonable long-term fit but slower to iterate while the metamodel design is still moving. Revisit after spec 001 ships.
- **LanceDB / pgvector / embedded graph DB.** Overkill at this scale and would add a second storage system.
- **Letting the server see code.** Explicit non-goal in `spec.md`.

## Dependencies to be added

(To be finalized by task 1.)

- `fastmcp`
- `strawberry-graphql` (code-first GraphQL).
- `fastembed` for CPU-only ONNX embeddings (verify the ONNX Runtime wheel is the CPU build).
- Node toolchain (build-time only): `vite`, `react`, `typescript`, `urql`.
