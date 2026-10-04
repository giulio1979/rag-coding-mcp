import { useEffect, useMemo, useState } from "react";
import { useClient, useQuery } from "urql";
import type { Client } from "urql";

/* ------------------------------------------------------------------ */
/* Metamodel query                                                     */
/* ------------------------------------------------------------------ */

const METAMODEL_QUERY = /* GraphQL */ `
  query Metamodel {
    metamodel {
      embeddingModel
      embeddingDim
      inventories {
        name
        description
        fields {
          name
          type
          required
          enumValues
        }
      }
      relations {
        name
        fromInventory
        toInventory
        cardinality
      }
    }
  }
`;

type Field = {
  name: string;
  type: string;
  required: boolean;
  enumValues: string[] | null;
};

type Inventory = {
  name: string;
  description: string;
  fields: Field[];
};

type Relation = {
  name: string;
  fromInventory: string;
  toInventory: string;
  cardinality: string;
};

type MetamodelData = {
  metamodel: {
    embeddingModel: string | null;
    embeddingDim: number | null;
    inventories: Inventory[];
    relations: Relation[];
  };
};

type NodeRow = {
  id: string;
  createdAt?: string;
  updatedAt?: string;
  [key: string]: unknown;
};

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

const BASE_FIELD_NAMES = [
  "id",
  "name",
  "description",
  "ownedBy",
  "updatedBy",
  "createdAt",
  "updatedAt",
];

/** Mirror of the server's `_field_name_for_inventory`. */
function inventoryFieldName(name: string): string {
  if (!name) return name;
  let i = 0;
  while (i < name.length && name[i] === name[i].toUpperCase() && /[A-Z]/.test(name[i])) i++;
  if (i === 0) return name;
  if (i === name.length) return name.toLowerCase();
  if (i > 1 && name[i] === name[i].toLowerCase()) {
    return name.slice(0, i - 1).toLowerCase() + name.slice(i - 1);
  }
  return name.slice(0, i).toLowerCase() + name.slice(i);
}

function pluralFieldName(name: string): string {
  return inventoryFieldName(name) + "s";
}

/** Deterministic hue per inventory name. */
function tileColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  return `hsl(${hue} 55% 42%)`;
}

function initials(name: string): string {
  const letters = name.match(/[A-Z]/g);
  if (letters && letters.length >= 2) return letters.slice(0, 2).join("");
  return name.slice(0, 2).toUpperCase();
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/** Pick the best "display name" field for a node. */
function displayName(node: NodeRow, inv: Inventory): string {
  for (const key of ["name", "title", "slug", "path", "fqName", "signature"]) {
    const v = node[key];
    if (typeof v === "string" && v.trim()) return v;
  }
  for (const f of inv.fields) {
    const v = node[f.name];
    if (typeof v === "string" && v.trim()) return v;
  }
  return node.id;
}

function displaySubtitle(node: NodeRow, inv: Inventory, primary: string): string {
  for (const key of ["description", "summary", "body", "content"]) {
    const v = node[key];
    if (typeof v === "string" && v.trim()) return v.slice(0, 160);
  }
  for (const f of inv.fields) {
    const v = node[f.name];
    if (typeof v === "string" && v.trim() && v !== primary) return v.slice(0, 160);
  }
  return node.id;
}

/* ------------------------------------------------------------------ */
/* Dynamic node query                                                  */
/* ------------------------------------------------------------------ */

function buildListQuery(inv: Inventory): string {
  const fieldSelection = [
    ...BASE_FIELD_NAMES,
    ...inv.fields.map((f) => f.name),
  ].join("\n        ");
  return /* GraphQL */ `
    query List_${inv.name}($text: String, $limit: Int, $offset: Int) {
      items: ${pluralFieldName(inv.name)}(text: $text, limit: $limit, offset: $offset) {
        score
        matchedBy
        node {
          ${fieldSelection}
        }
      }
    }
  `;
}

type ListResult = {
  score: number;
  matchedBy: string;
  node: NodeRow;
};

async function fetchList(
  client: Client,
  inv: Inventory,
  text: string | null,
): Promise<NodeRow[]> {
  const result = await client
    .query<{ items: ListResult[] }>(
      buildListQuery(inv),
      { text: text || null, limit: 200, offset: 0 },
      { requestPolicy: "network-only" },
    )
    .toPromise();
  if (result.error) throw new Error(result.error.message);
  return (result.data?.items ?? []).map((r) => r.node);
}

/* ------------------------------------------------------------------ */
/* Root App                                                            */
/* ------------------------------------------------------------------ */

export function App() {
  const [{ data, fetching, error }] = useQuery<MetamodelData>({ query: METAMODEL_QUERY });

  const inventories = data?.metamodel.inventories ?? [];
  const relations = data?.metamodel.relations ?? [];

  const [activeName, setActiveName] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ inventory: string; id: string } | null>(null);
  // { [inventoryName]: { [fieldName]: string[] } }
  const [enumFilters, setEnumFilters] = useState<Record<string, Record<string, string[]>>>({});

  useEffect(() => {
    if (!activeName && inventories.length > 0) {
      setActiveName(inventories[0].name);
    }
  }, [inventories, activeName]);

  const active = inventories.find((i) => i.name === activeName) ?? null;

  function openNode(inventory: string, id: string) {
    setSelected({ inventory, id });
  }

  function toggleEnumFilter(inv: string, field: string, value: string) {
    setEnumFilters((prev) => {
      const byField = { ...(prev[inv] ?? {}) };
      const current = byField[field] ?? [];
      const next = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value];
      if (next.length === 0) delete byField[field];
      else byField[field] = next;
      const out = { ...prev };
      if (Object.keys(byField).length === 0) delete out[inv];
      else out[inv] = byField;
      return out;
    });
  }

  function clearFilters(inv: string) {
    setEnumFilters((prev) => {
      const { [inv]: _drop, ...rest } = prev;
      void _drop;
      return rest;
    });
  }

  const activeFilters = active ? enumFilters[active.name] ?? {} : {};

  return (
    <div className="app">
      <nav className="nav">
        <button className="item active" title="Inventory">◼</button>
        <button className="item" title="Reports (coming soon)">▦</button>
        <button className="item" title="Diagrams (coming soon)">◈</button>
        <button className="item" title="Admin (coming soon)">⚙</button>
      </nav>

      <header className="topbar">
        <div className="brand">rag-coding-mcp</div>
        <div className="search">
          <input placeholder="Global search coming soon…" disabled />
        </div>
        <div className="spacer" />
        <a className="btn" href="/graphql-ui" target="_blank" rel="noreferrer">
          GraphQL ↗
        </a>
        <div className="meta" style={{ marginLeft: 12 }}>
          {data?.metamodel.embeddingModel ? (
            <>
              embedding: <code>{data.metamodel.embeddingModel}</code>
              {" · "}dim <code>{data.metamodel.embeddingDim}</code>
            </>
          ) : (
            <span className="muted">no embedder</span>
          )}
        </div>
      </header>

      <aside className="filters">
        <h4>Inventories</h4>
        {fetching && <div className="loading">Loading metamodel…</div>}
        {error && <div className="error-banner">{error.message}</div>}
        <div className="type-list">
          {inventories.map((inv) => (
            <TypeRow
              key={inv.name}
              inv={inv}
              active={inv.name === activeName}
              onClick={() => setActiveName(inv.name)}
            />
          ))}
        </div>

        {active && (
          <FilterSection
            inventory={active}
            selections={activeFilters}
            onToggle={(field, value) => toggleEnumFilter(active.name, field, value)}
            onClear={() => clearFilters(active.name)}
          />
        )}
      </aside>

      <section className="results">
        {active ? (
          <InventoryView
            inventory={active}
            relations={relations}
            enumFilters={activeFilters}
            onOpenNode={(id) => openNode(active.name, id)}
          />
        ) : (
          <div className="empty">Select an inventory on the left.</div>
        )}
      </section>

      {selected && (
        <DetailPane
          target={selected}
          inventories={inventories}
          relations={relations}
          onNavigate={openNode}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Filter panel: type row                                              */
/* ------------------------------------------------------------------ */

function TypeRow({
  inv,
  active,
  onClick,
}: {
  inv: Inventory;
  active: boolean;
  onClick: () => void;
}) {
  const [count, setCount] = useState<number | null>(null);
  const client = useClient();

  useEffect(() => {
    let cancelled = false;
    fetchList(client, inv, null)
      .then((rows) => {
        if (!cancelled) setCount(rows.length);
      })
      .catch(() => {
        if (!cancelled) setCount(null);
      });
    return () => {
      cancelled = true;
    };
  }, [client, inv]);

  return (
    <button className={`type-row ${active ? "active" : ""}`} onClick={onClick}>
      <span
        className="tile md"
        style={{ background: tileColor(inv.name) }}
        aria-hidden
      >
        {initials(inv.name)}
      </span>
      <span className="name">{inv.name}</span>
      <span className="count">{count ?? "·"}</span>
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Inventory view                                                      */
/* ------------------------------------------------------------------ */

type ViewMode = "list" | "table";

function InventoryView({
  inventory,
  relations,
  enumFilters,
  onOpenNode,
}: {
  inventory: Inventory;
  relations: Relation[];
  enumFilters: Record<string, string[]>;
  onOpenNode: (id: string) => void;
}) {
  void relations;
  const client = useClient();
  const [rows, setRows] = useState<NodeRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [committedText, setCommittedText] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("list");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchList(client, inventory, committedText)
      .then((r) => {
        if (!cancelled) setRows(r);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, inventory, committedText]);

  const filteredRows = useMemo(() => {
    const entries = Object.entries(enumFilters);
    if (entries.length === 0) return rows;
    return rows.filter((row) =>
      entries.every(([field, allowed]) => {
        const v = row[field];
        return typeof v === "string" && allowed.includes(v);
      }),
    );
  }, [rows, enumFilters]);

  const columns = useMemo(() => {
    const extras = inventory.fields
      .map((f) => f.name)
      .filter((c) => c !== "name" && c !== "title")
      .slice(0, 3);
    return ["name", ...extras, "updatedAt"];
  }, [inventory]);

  return (
    <>
      <div className="results-header">
        <span
          className="tile lg"
          style={{ background: tileColor(inventory.name) }}
          aria-hidden
        >
          {initials(inventory.name)}
        </span>
        <div>
          <div className="title">{inventory.name}</div>
          {inventory.description && (
            <div className="muted" style={{ fontSize: 12.5 }}>
              {inventory.description}
            </div>
          )}
        </div>
        <div style={{ marginLeft: 8 }} className="count">
          {filteredRows.length.toLocaleString()}
          {filteredRows.length !== rows.length && (
            <span className="muted"> / {rows.length}</span>
          )}
        </div>
        <div className="actions">
          <div className="view-toggle">
            <button className={view === "list" ? "active" : ""} onClick={() => setView("list")}>
              List
            </button>
            <button className={view === "table" ? "active" : ""} onClick={() => setView("table")}>
              Table
            </button>
          </div>
        </div>
      </div>

      <form
        className="chipbar"
        onSubmit={(e) => {
          e.preventDefault();
          setCommittedText(text.trim() ? text.trim() : null);
        }}
      >
        <span className="chip type-chip">
          <span
            className="tile sm"
            style={{ background: tileColor(inventory.name) }}
            aria-hidden
          >
            {initials(inventory.name)}
          </span>
          <strong>Type:</strong> {inventory.name}
        </span>
        <input
          value={text}
          placeholder="Filter by text (lexical + semantic)…"
          onChange={(e) => setText(e.target.value)}
          style={{
            flex: 1,
            minWidth: 240,
            height: 24,
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-pill)",
            padding: "0 12px",
            fontSize: 12,
            background: "#fff",
            color: "var(--text)",
          }}
        />
        {committedText && (
          <span className="chip">
            <strong>Text:</strong> {committedText}
            <button
              type="button"
              onClick={() => {
                setText("");
                setCommittedText(null);
              }}
            >
              ×
            </button>
          </span>
        )}
        <button className="btn" type="submit">
          Apply
        </button>
      </form>

      {error && <div className="error-banner">{error}</div>}
      {loading && <div className="loading">Loading…</div>}

      {!loading && filteredRows.length === 0 && !error && (
        <div className="empty">No {inventory.name} nodes match.</div>
      )}

      {!loading && filteredRows.length > 0 && view === "list" && (
        <div className="result-list">
          {filteredRows.map((row) => {
            const name = displayName(row, inventory);
            const sub = displaySubtitle(row, inventory, name);
            return (
              <div
                key={row.id}
                className="result-row"
                onClick={() => onOpenNode(row.id)}
                role="button"
              >
                <span
                  className="tile lg"
                  style={{ background: tileColor(inventory.name) }}
                  aria-hidden
                >
                  {initials(inventory.name)}
                </span>
                <div className="main">
                  <div className="name">{name}</div>
                  <div className="sub">{sub}</div>
                </div>
                <div className="meta">
                  <span className="chip type-chip">{inventory.name}</span>
                  <span>{formatTimestamp(row.updatedAt)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && filteredRows.length > 0 && view === "table" && (
        <div className="result-table">
          <table>
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c}>{humanizeColumn(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => (
                <tr key={row.id} onClick={() => onOpenNode(row.id)}>
                  {columns.map((c) => (
                    <td key={c}>
                      {c === "name"
                        ? displayName(row, inventory)
                        : c === "updatedAt"
                          ? formatTimestamp(row.updatedAt)
                          : formatValue(row[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Detail slide-over                                                   */
/* ------------------------------------------------------------------ */

type RelatedStub = { id: string; name: string };

function traversalFieldName(relation: string, direction: "out" | "in"): string {
  const parts = relation.split("_");
  const camel = parts[0] + parts.slice(1).map((p) => p[0].toUpperCase() + p.slice(1)).join("");
  return direction === "out" ? camel : camel + "In";
}

function buildDetailQuery(inv: Inventory, relations: Relation[]): string {
  const scalarFields = [
    ...BASE_FIELD_NAMES,
    ...inv.fields.map((f) => f.name),
  ].join("\n        ");
  const outgoing = relations.filter((r) => r.fromInventory === inv.name);
  const incoming = relations.filter((r) => r.toInventory === inv.name);
  const relationFields = [
    ...outgoing.map(
      (r) => `${traversalFieldName(r.name, "out")}: ${traversalFieldName(r.name, "out")} { id name }`,
    ),
    ...incoming.map(
      (r) => `${traversalFieldName(r.name, "in")}: ${traversalFieldName(r.name, "in")} { id name }`,
    ),
  ].join("\n        ");
  const singular = inventoryFieldName(inv.name);
  return /* GraphQL */ `
    query Node_${inv.name}($id: ID!) {
      node: ${singular}(id: $id) {
        ${scalarFields}
        ${relationFields}
      }
    }
  `;
}

function DetailPane({
  target,
  inventories,
  relations,
  onNavigate,
  onClose,
}: {
  target: { inventory: string; id: string };
  inventories: Inventory[];
  relations: Relation[];
  onNavigate: (inventory: string, id: string) => void;
  onClose: () => void;
}) {
  const client = useClient();
  const [node, setNode] = useState<NodeRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const inventory = inventories.find((i) => i.name === target.inventory) ?? null;

  useEffect(() => {
    if (!inventory) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNode(null);
    client
      .query<{ node: NodeRow | null }>(
        buildDetailQuery(inventory, relations),
        { id: target.id },
        { requestPolicy: "network-only" },
      )
      .toPromise()
      .then((r) => {
        if (cancelled) return;
        if (r.error) setError(r.error.message);
        else setNode(r.data?.node ?? null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [client, inventory, relations, target.id]);

  if (!inventory) {
    return (
      <>
        <div className="overlay" onClick={onClose} />
        <aside className="detail" role="dialog">
          <header className="detail-header">
            <div className="title">Unknown inventory</div>
            <button className="btn ghost" onClick={onClose}>✕</button>
          </header>
        </aside>
      </>
    );
  }

  const outgoing = relations.filter((r) => r.fromInventory === inventory.name);
  const incoming = relations.filter((r) => r.toInventory === inventory.name);
  const name = node ? displayName(node, inventory) : target.id;

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="detail" role="dialog" aria-label={`${inventory.name} detail`}>
        <header className="detail-header">
          <span
            className="tile lg"
            style={{ background: tileColor(inventory.name) }}
            aria-hidden
          >
            {initials(inventory.name)}
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="title">{name}</div>
            <div className="type-label">{inventory.name}</div>
          </div>
          <button className="btn ghost" onClick={onClose}>✕</button>
        </header>
        <div className="detail-body">
          {loading && <div className="loading">Loading…</div>}
          {error && <div className="error-banner">{error}</div>}
          {!loading && !error && !node && <div className="empty">Not found.</div>}

          {node && (
            <>
              <section className="detail-section">
                <h3>Identity</h3>
                <dl className="kv">
                  <dt>id</dt>
                  <dd><code>{node.id}</code></dd>
                  {typeof node.createdAt === "string" && (
                    <>
                      <dt>created</dt>
                      <dd>{formatTimestamp(node.createdAt)}</dd>
                    </>
                  )}
                  {typeof node.updatedAt === "string" && (
                    <>
                      <dt>updated</dt>
                      <dd>{formatTimestamp(node.updatedAt)}</dd>
                    </>
                  )}
                </dl>
              </section>

              {inventory.fields.length > 0 && (
                <section className="detail-section">
                  <h3>Fields</h3>
                  <dl className="kv">
                    {inventory.fields.map((f) => (
                      <FieldRow key={f.name} field={f} column={f.name} value={node[f.name]} />
                    ))}
                  </dl>
                </section>
              )}

              {outgoing.length > 0 && (
                <section className="detail-section">
                  <h3>Relations · outgoing</h3>
                  {outgoing.map((r) => {
                    const key = traversalFieldName(r.name, "out");
                    const items = (node[key] as RelatedStub[] | undefined) ?? [];
                    return (
                      <RelationGroup
                        key={`o-${r.name}`}
                        label={r.name}
                        arrow="→"
                        targetInventory={r.toInventory}
                        items={items}
                        onOpen={(id) => onNavigate(r.toInventory, id)}
                      />
                    );
                  })}
                </section>
              )}

              {incoming.length > 0 && (
                <section className="detail-section">
                  <h3>Relations · incoming</h3>
                  {incoming.map((r) => {
                    const key = traversalFieldName(r.name, "in");
                    const items = (node[key] as RelatedStub[] | undefined) ?? [];
                    return (
                      <RelationGroup
                        key={`i-${r.name}`}
                        label={r.name}
                        arrow="←"
                        targetInventory={r.fromInventory}
                        items={items}
                        onOpen={(id) => onNavigate(r.fromInventory, id)}
                      />
                    );
                  })}
                </section>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}

function RelationGroup({
  label,
  arrow,
  targetInventory,
  items,
  onOpen,
}: {
  label: string;
  arrow: "→" | "←";
  targetInventory: string;
  items: RelatedStub[];
  onOpen: (id: string) => void;
}) {
  return (
    <div className="relation-group">
      <div className="relation-head">
        <code>{label}</code> <span className="muted">{arrow} {targetInventory}</span>
        <span className="count muted" style={{ marginLeft: "auto" }}>
          {items.length}
        </span>
      </div>
      {items.length === 0 ? (
        <div className="muted" style={{ padding: "4px 8px", fontSize: 12 }}>
          none
        </div>
      ) : (
        <ul className="relation-list">
          {items.map((it) => (
            <li
              key={it.id}
              className="relation-item"
              onClick={() => onOpen(it.id)}
              role="button"
            >
              <span
                className="tile sm"
                style={{ background: tileColor(targetInventory) }}
                aria-hidden
              >
                {initials(targetInventory)}
              </span>
              <span className="name">{it.name || it.id}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FieldRow({
  field,
  column,
  value,
}: {
  field: Field;
  column: string;
  value: unknown;
}) {
  return (
    <>
      <dt>
        {column}
        {field.required && (
          <span className="chip required" style={{ marginLeft: 6, height: 16, padding: "0 6px", fontSize: 10 }}>
            req
          </span>
        )}
      </dt>
      <dd>
        {value === null || value === undefined || value === "" ? (
          <span className="muted">—</span>
        ) : typeof value === "string" && value.length > 160 ? (
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto" }}>
            {value}
          </pre>
        ) : (
          formatValue(value)
        )}
      </dd>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Left-sidebar filter section                                         */
/* ------------------------------------------------------------------ */

function FilterSection({
  inventory,
  selections,
  onToggle,
  onClear,
}: {
  inventory: Inventory;
  selections: Record<string, string[]>;
  onToggle: (field: string, value: string) => void;
  onClear: () => void;
}) {
  const enumFields = inventory.fields.filter(
    (f) => f.type === "enum" && f.enumValues && f.enumValues.length > 0,
  );
  const activeCount = Object.values(selections).reduce((n, vs) => n + vs.length, 0);

  return (
    <>
      <h4 style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ flex: 1 }}>Filters</span>
        {activeCount > 0 && (
          <button className="btn ghost" style={{ height: 20, padding: "0 6px", fontSize: 11 }} onClick={onClear}>
            Clear
          </button>
        )}
      </h4>
      {enumFields.length === 0 && (
        <div className="muted" style={{ padding: "0 8px", fontSize: 12 }}>
          No filterable fields on {inventory.name}.
        </div>
      )}
      {enumFields.map((f) => {
        const picked = selections[f.name] ?? [];
        return (
          <div key={f.name} className="filter-group">
            <div className="filter-group-head">{f.name}</div>
            {(f.enumValues ?? []).map((v) => {
              const on = picked.includes(v);
              return (
                <label key={v} className={`filter-check ${on ? "on" : ""}`}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => onToggle(f.name, v)}
                  />
                  <span>{v}</span>
                </label>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Small formatters                                                    */
/* ------------------------------------------------------------------ */

function humanizeColumn(c: string): string {
  return c
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (s) => s.toUpperCase())
    .trim();
}

function formatTimestamp(v: unknown): string {
  if (typeof v !== "string" || !v) return "—";
  try {
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return v;
    return d.toISOString().replace("T", " ").slice(0, 16);
  } catch {
    return v;
  }
}
