# 001 — Tasks

Ordered, atomic tasks. Check off as you go. Each task should be reviewable in one sitting.

Do **not** start these until `spec.md` is `Status: approved` and `plan.md` is signed off.

## Setup

- [x] 1. Initialize Python project: `pyproject.toml`, Python 3.11+, `uv`, add `fastmcp`, GraphQL library (`strawberry` or `ariadne`), `fastembed`. Commit lockfile.
- [x] 2. Add `src/rag_coding_mcp/` package skeleton and `tests/` mirror. Add minimal pytest config.
- [x] 3. Add `ruff` config.
- [x] 4. Initialize `ui/` subproject: Vite + React + TypeScript, pick a GraphQL client (`urql` or `graphql-request`). Commit lockfile.

## Storage core

- [x] 5. Implement `db.connect(path)` opening SQLite in WAL mode and returning a connection.
- [x] 6. Create fixed support tables: `users`, `metamodel_inventories`, `metamodel_fields`, `metamodel_relations`, `edges`, `embeddings`, `node_fts`. Idempotent on startup.
- [x] 7. Seed `users` with a single `admin` row (`is_admin = true`) when the table is empty.
- [x] 8. Seed the built-in metamodel (`Repo`, `File`, `Method`, `SOP` and the declared relations) when `metamodel_inventories` is empty. Create the corresponding inventory tables, each with base columns including `owned_by` and `updated_by` (FK → `users.id`).
- [x] 9. Tests: empty DB seeds correctly; second startup is a no-op; each seeded inventory table has the expected columns including attribution.

## Actor context

- [x] 10. Implement a request-scoped context that extracts `X-Actor-Id` from the HTTP request, resolves it against `users`, and falls back to the seeded `admin` when absent or unknown.
- [x] 11. Tests: present-and-valid, present-and-unknown, absent all resolve to the expected user id.

## Metamodel operations (storage-level)

- [x] 12. `createInventory(name, fields, description)` — DDL + metamodel rows, single transaction. Tests cover duplicate name and invalid field types.
- [x] 13. `addField(inventory, name, type, enum_values?, required, description)` — `ALTER TABLE ADD COLUMN` + metamodel row. Tests cover existing rows preserved.
- [x] 14. `removeField(inventory, name)` — `ALTER TABLE DROP COLUMN` + metamodel row; refuse when any row has a non-null value for the column, when it is one of the base columns, and when it is referenced by an index or trigger. Tests cover each.
- [x] 15. `createRelationType(name, from_inventory, to_inventory, cardinality, description)` — metamodel row only. Tests cover duplicate name and unknown inventory.
- [x] 16. `removeRelationType(name)` — refuse if any edges exist; test both paths.

## Embeddings

- [x] 17. Pick and wire `fastembed` CPU-only. Expose `embed(texts) -> list[bytes]` plus `model_name` and `dim`.
- [x] 18. On node create/update, compute embedding from `name + "\n" + description` and upsert into `embeddings`. Tests: embedding is deterministic, dimension matches reported `dim`, `model` column is populated.
- [x] 19. Admin helper: iterate stale embeddings (model != current) and re-embed. Test on a mixed-model dataset.

## Search primitives

- [x] 20. CamelCase/snake_case splitter; unit tests cover `getUserName`, `get_user_name`, `HTTPServer`.
- [x] 21. FTS5 insert/update/delete triggered by node writes using the splitter. Tests include the identifier round-trip.
- [x] 22. Cosine similarity scan over `embeddings` filtered by inventory. Tests cover ordering and empty-set behavior.
- [x] 23. Reciprocal rank fusion; returns results tagged with `matchedBy ∈ {LEXICAL, SEMANTIC, BOTH}`. Tests cover each case.

## GraphQL schema generation

- [x] 24. From the metamodel, generate GraphQL types for every inventory, including base fields (`id`, `name`, `description`, `ownedBy`, `updatedBy`, `createdAt`, `updatedAt`), declared fields, and relation traversal fields.
- [x] 25. Generate `x(id)` and `xs(filter, text, similarTo, limit, offset)` queries per inventory; `XResult` carries `matchedBy`.
- [x] 26. Generate data mutations `createX`, `updateX`, `deleteX`, and generic `connect` / `disconnect`. Resolvers read the actor from the request context and set `owned_by` on create and `updated_by` on both create and update. Tests cover attribution across create, update and admin fallback.
- [x] 27. Expose metamodel mutations `createInventory`, `addField`, `removeField`, `createRelationType`, `removeRelationType` on the root. Each triggers a schema rebuild on success.
- [x] 28. Add the user management slice: `users` query, `createUser`, `renameUser`, `setUserAdmin`. `User` is a GraphQL type but not an inventory and does not appear in `get_metamodel`'s inventory list. Tests cover each.
- [x] 29. Tests: schema reflects a freshly-added inventory on the next request; in-flight queries don't crash.

## MCP tools and HTTP endpoint

- [x] 30. Implement `get_metamodel`. Returns inventories, fields, relations, GraphQL SDL (printed), embedding model + dim. The user management slice is included in the SDL but users are not listed as an inventory. Tests cover a fresh DB and a DB after a `createInventory`.
- [x] 31. Implement `graphql(query, variables?)`. Delegates to the generated schema. Tests cover a query, a data mutation, a metamodel mutation, and a user mutation.
- [x] 32. Expose a plain HTTP `POST /graphql` endpoint sharing the same schema and resolvers, for the admin UI. Reads `X-Actor-Id` through the same context. Tests cover parity with the MCP tool for one representative mutation.
- [x] 33. Enforce exactly two MCP tools; a sentinel test asserts no third tool is reachable.

## Invariants

- [ ] 34. Assert via test that no inventory table has a column that could carry raw file contents, except `SOP.body`. Documented list of allowed text columns.
- [ ] 35. Assert via test that `File` writes store only `name`, `description`, `path`, `content_hash`, the attribution columns and base timestamps.

## Admin UI

- [ ] 36. Scaffold `ui/` routing: left-pane inventory list (plus a fixed Users entry), right-pane node list.
- [ ] 37. Metamodel-driven forms: generate a create/edit form for a node from the inventory's field definitions. Supports string, int, bool, enum, text.
- [ ] 38. Node list features: pagination, per-column filter, free-text (`text`) and semantic (`similarTo`) search fields, delete with confirmation.
- [ ] 39. Edge management: on a node's detail view, list outgoing/incoming edges, create via a relation-type picker (`connect`), delete via `disconnect`.
- [ ] 40. Metamodel CRUD: UI to create inventories, add/remove fields, create/remove relation types, each with a confirmation step that surfaces the server's structured error on failure.
- [ ] 41. User management view: list, create, rename, toggle admin. The UI stores a current actor selection locally and sends it as `X-Actor-Id` with every request.
- [ ] 42. E2E test (Playwright or similar): create an inventory via the UI, create a node in it, verify it appears via `get_metamodel` + a GraphQL query.

## Server + transport

- [ ] 43. Mount `get_metamodel` and `graphql` on the FastMCP streamable-HTTP route. Mount `/graphql` HTTP endpoint. Mount `/ui` serving the built SPA assets with SPA fallback (serve `index.html` for unknown subpaths). No auth.
- [ ] 44. Smoke test: start the server, load `/ui`, call `get_metamodel`, run a GraphQL mutation end-to-end.

## Packaging and deploy

- [ ] 45. Multi-stage Dockerfile: Node builder stage for `ui/`, Python builder stage, final Debian slim runtime, non-root, `/data` writable.
- [ ] 46. k3s manifests: Deployment + PVC + Service + NetworkPolicy restricting ingress to the agentgateway pod.
- [ ] 47. agentgateway configuration (external): one route covering MCP, `/graphql` and `/ui` against this backend.
- [ ] 48. Document deploy steps once validated on the NAS.

## Ship

- [ ] 49. Walk every acceptance criterion in `spec.md` and point to the test proving it.
- [ ] 50. Run the audit checklist from `AGENTS.md` §4.4. Record findings in `notes.md`.
- [ ] 51. Flip `spec.md` status to `shipped` and update `AGENTS.md` §7 table.
