# 001 — Core MCP Server

- Status: in-progress
- Owner: unassigned
- Updated: 2026-10-04

## Problem

A developer uses several editors across several laptops, plus phones for reading on the go. Each editor's AI coding agent currently builds up its own private understanding of each repository, with no sharing between editors, no shared record of past decisions, and no way to read that knowledge from a phone. Local-only, per-editor memory also means the same summarization and indexing work gets repeated.

## Goal

A single, central MCP server, running on the home NAS under k3s, that holds durable, structured knowledge **about** a set of repositories as a typed graph. Every editor on every laptop talks to the same server through [agentgateway](https://agentgateway.dev/) at `gateway.bizrader.uk` (→ `192.168.178.208`). Phones reach the same endpoint.

The server is a knowledge store, not an executor.

## Non-goals

- **The server does not see or store source code.** Agents keep code local. The server stores names, descriptions, signatures and relations.
- **The server does not run builds, tests, lints or any user code.** Agents run those locally and report results by writing nodes/edges.
- **No multi-tenant isolation.** Single user, single trust domain. Auth and TLS are agentgateway's job.
- **No real-time collaboration primitives** (presence, locks, streaming diffs). Writes are last-write-wins per node identity.

## Model: inventories, nodes, edges

The data is a typed graph, LeanIX-style:

- An **inventory** is a type of node (e.g. `Repo`, `File`, `Method`, `SOP`). New inventories can be added at runtime.
- A **node** is an instance of an inventory. Every node has at minimum:
  - `id` — stable, server-assigned.
  - `name` — short identifier, unique within its inventory and parent scope.
  - `description` — free text, may be empty.
  - `created_at`, `updated_at`.
  - `owned_by`, `updated_by` — user IDs recording who created and last modified the node.
  - Additional typed fields defined by its inventory's metamodel entry.
- A **relation type** is a declared directed edge between two inventories, with a name and cardinality. Instances of relation types are **edges** between nodes.
- Every node has one **embedding** computed server-side from `name + "\n" + description`, stored with the embedding model name and dimension.

### Built-in metamodel (seeded at first start)

Inventories:

- `Repo` — fields: (none beyond base). Represents a repository.
- `File` — fields: `path` (string), `content_hash` (string).
- `Method` — fields: `signature` (string), `kind` (enum: `function | method | class | module`).
- `SOP` — a Standard Operating Procedure document for a repo. Fields: `category` (enum: `testing | deploy | building | coding | running`, extensible), `body` (text).

Relation types:

- `Repo —contains→ File`
- `File —defines→ Method`
- `Method —calls→ Method` (optional, 0..n)
- `Repo —has_sop→ SOP`
- `SOP —references_file→ File` (optional)
- `SOP —references_method→ Method` (optional)

This seeded set is the starting point; agents can extend it through metamodel mutations.

### Users and attribution

Users are **not** an inventory. They live in a dedicated `users` table (`id`, `name`, `is_admin`, `created_at`) that is not exposed through generic graph operations. The server seeds one record on first start: `admin`, flagged `is_admin = true`.

Every write (data or metamodel mutation) is attributed:

- The caller sends an `X-Actor-Id` HTTP header naming an existing user.
- The server resolves it, writes `owned_by` on create, and writes `updated_by` on both create and update.
- If the header is absent or names an unknown user, the write is attributed to the seeded `admin`.
- `owned_by` is set once on create and never changed by `update<Inventory>`.

User management lives on a small dedicated area of the GraphQL schema (`users` query, `createUser` / `renameUser` / `setUserAdmin` mutations). There is no password or token layer in the server itself; authentication remains agentgateway's job.

## Tools (MCP surface)

Exactly **two** tools.

### 1. `get_metamodel()`

Returns the current metamodel as a document:

- The list of inventories with their fields (name, type, enum values, required/optional, description).
- The list of relation types (name, from, to, cardinality, description).
- The GraphQL schema (SDL) that `graphql` accepts, derived from the metamodel.
- The embedding model name and dimension in use.

This is the tool agents call first to learn what they can query and write.

### 2. `graphql(query, variables?)`

A single GraphQL endpoint over the graph. It supports:

- **Queries** over inventories and relations, including filtering by field equality, free-text, and semantic similarity.
- **Data mutations:** `create<Inventory>`, `update<Inventory>`, `delete<Inventory>`, `connect(from_id, relation, to_id)`, `disconnect(…)`.
- **Metamodel mutations:** `createInventory`, `addField`, `removeField`, `createRelationType`, `removeRelationType`. These change the metamodel atomically; the GraphQL schema returned by `get_metamodel` reflects the change on the next call.

The server exposes no other tools. Everything an agent needs — reading, writing, extending the schema — goes through `graphql`.

## Admin UI

The server also exposes a browser-based admin UI at the `/ui` path on the same hostname, intended for inspection and debugging. It is a React + Vite single-page app whose built assets are baked into the server container; the Python server serves the static files. All UI operations go through the same `graphql` endpoint; the UI has no privileged backdoor.

UI capabilities:

- Left pane: list of inventories (plus a fixed entry for `Users`), reflecting the current metamodel.
- Right pane: paginated node list for the selected inventory, with per-column filters, free-text and semantic search fields, and relation navigation.
- Full CRUD on nodes and edges.
- Full metamodel CRUD: create/edit/delete inventories, fields and relation types. Destructive actions require a confirmation step and surface the server's structured error when a change would orphan data.
- User management view (list, create, rename, toggle admin).

The UI is reachable at the same hostname as MCP. Access control (who can load `/ui` at all) is agentgateway's responsibility; from the server's point of view the UI is just a GraphQL client.

## Search behavior

Search is a GraphQL concern, not a separate tool. The schema offers two orthogonal filters on every inventory's list query:

- `text: String` — lexical match over `name + description` (and any text fields) using SQLite FTS5. Identifier splitting on camelCase and snake_case is applied at index time, so `getUserName` matches a node called `get_user_name`.
- `similarTo: String` — semantic match: the server embeds the query at call time and ranks nodes by cosine similarity against stored node embeddings.

Both filters can be combined. When both are supplied, results are merged by reciprocal rank fusion and each result carries a `matchedBy: LEXICAL | SEMANTIC | BOTH` field. Standard GraphQL filters (equality on fields, relation traversal) can be composed with either.

## Workflow the server encourages

The server shapes agent behavior through the metamodel description and GraphQL schema docstrings, not through enforcement. A typical session:

1. Agent calls `get_metamodel` once per session to learn the current schema.
2. Agent runs a GraphQL query to orient (e.g. list `Repo` nodes with their `SOP` and `File` counts).
3. Agent runs scoped queries combining relation traversal with `text` and `similarTo`.
4. Agent syncs local view by querying `File` nodes with their `content_hash`, computing diffs locally, and sending `createFile` / `updateFile` mutations.
5. Agent writes or updates `SOP` nodes to record decisions (including `running` SOPs that document how a repo is run). Transient execution output (test logs, build output) is **not** stored on the server; agents keep that local.

## Acceptance criteria

A spec is "done" when each of these can be demonstrated with an automated test or a short recorded transcript against a running instance:

1. The MCP server exposes exactly two tools: `get_metamodel` and `graphql`. Attempting to call any other tool fails.
2. `get_metamodel` returns inventories, fields, relation types, GraphQL SDL, and embedding model name + dimension, all consistent with the current database.
3. On a fresh database, `get_metamodel` returns the seeded built-in metamodel described above.
4. A GraphQL `createInventory` mutation adds a new inventory; a subsequent `get_metamodel` reflects it; a corresponding SQLite table exists.
5. A GraphQL `addField` mutation on an existing inventory adds a column to its table and the GraphQL schema; existing rows remain intact.
6. `removeField` and `removeRelationType` succeed when no data depends on them and fail with a structured error when data would be orphaned.
7. Creating a node computes an embedding from `name + description`; updating either field re-computes it. The stored row carries the current embedding model name and dimension.
8. Changing the configured embedding model causes a subsequent `get_metamodel` to report the new model; nodes whose embeddings were produced by the old model are flagged as stale and are re-embedded on next read or by an admin trigger.
9. A list query with `text: "getUserName"` matches a node whose name is `get_user_name`, and vice versa.
10. A list query with `similarTo: "<text>"` returns nodes ordered by cosine similarity, each with `matchedBy = SEMANTIC`. With both `text` and `similarTo` supplied, results carry `matchedBy ∈ {LEXICAL, SEMANTIC, BOTH}` and are merged by reciprocal rank fusion.
11. Writing a `File` node stores only `path`, `content_hash`, `name`, `description`, the base attribution columns — never file contents. Verified by inspecting the database after a representative sync.
12. The server has no authentication of its own. Deployed behind agentgateway, it is reachable only from the gateway pod (enforced by k3s NetworkPolicy), and survives a pod restart with all graph data intact on a PVC-backed SQLite file.
13. On a fresh database a single `admin` user exists with `is_admin = true`. Mutations with no `X-Actor-Id` header are attributed to `admin`; mutations with a valid header are attributed to the referenced user; `owned_by` is set once on create and preserved across updates.
14. The `/ui` path serves the built SPA. Loading it in a browser renders the inventory list from `get_metamodel`, and performing CRUD on a node and on a metamodel entry through the UI results in the same database state as performing the equivalent GraphQL operations directly.

## Open questions

Tracked in `notes.md`. Resolve before `Status: approved`.
