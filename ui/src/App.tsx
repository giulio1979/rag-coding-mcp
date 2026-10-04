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

  useEffect(() => {
    if (!activeName && inventories.length > 0) {
      setActiveName(inventories[0].name);
    }
  }, [inventories, activeName]);

  const active = inventories.find((i) => i.name === activeName) ?? null;

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
        <div className="meta">
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

        {relations.length > 0 && (
          <>
            <h4>Relations</h4>
            <div className="type-list">
              {relations.map((r) => (
                <div key={r.name} className="type-row" title={r.cardinality}>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {r.fromInventory} → {r.toInventory}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </aside>

      <section className="results">
        {active ? (
          <InventoryView inventory={active} relations={relations} />
        ) : (
          <div className="empty">Select an inventory on the left.</div>
        )}
      </section>
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
}: {
  inventory: Inventory;
  relations: Relation[];
}) {
  const client = useClient();
  const [rows, setRows] = useState<NodeRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [committedText, setCommittedText] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>("list");
  const [selected, setSelected] = useState<NodeRow | null>(null);

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
          {rows.length.toLocaleString()}
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

      {!loading && rows.length === 0 && !error && (
        <div className="empty">No {inventory.name} nodes yet.</div>
      )}

      {!loading && rows.length > 0 && view === "list" && (
        <div className="result-list">
          {rows.map((row) => {
            const name = displayName(row, inventory);
            const sub = displaySubtitle(row, inventory, name);
            return (
              <div
                key={row.id}
                className="result-row"
                onClick={() => setSelected(row)}
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

      {!loading && rows.length > 0 && view === "table" && (
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
              {rows.map((row) => (
                <tr key={row.id} onClick={() => setSelected(row)}>
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

      {selected && (
        <DetailPane
          node={selected}
          inventory={inventory}
          relations={relations}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Detail slide-over                                                   */
/* ------------------------------------------------------------------ */

function DetailPane({
  node,
  inventory,
  relations,
  onClose,
}: {
  node: NodeRow;
  inventory: Inventory;
  relations: Relation[];
  onClose: () => void;
}) {
  const name = displayName(node, inventory);
  const outgoing = relations.filter((r) => r.fromInventory === inventory.name);
  const incoming = relations.filter((r) => r.toInventory === inventory.name);

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
          <button className="btn ghost" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="detail-body">
          <section className="detail-section">
            <h3>Identity</h3>
            <dl className="kv">
              <dt>id</dt>
              <dd><code>{node.id}</code></dd>
              {node.createdAt && (
                <>
                  <dt>created</dt>
                  <dd>{formatTimestamp(node.createdAt)}</dd>
                </>
              )}
              {node.updatedAt && (
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

          {(outgoing.length > 0 || incoming.length > 0) && (
            <section className="detail-section">
              <h3>Relations</h3>
              <dl className="kv">
                {outgoing.map((r) => (
                  <RelationRow key={`o-${r.name}`} label={`${r.name} →`} target={r.toInventory} card={r.cardinality} />
                ))}
                {incoming.map((r) => (
                  <RelationRow key={`i-${r.name}`} label={`← ${r.name}`} target={r.fromInventory} card={r.cardinality} />
                ))}
              </dl>
            </section>
          )}
        </div>
      </aside>
    </>
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
        {field.required && <span className="chip required" style={{ marginLeft: 6, height: 16, padding: "0 6px", fontSize: 10 }}>req</span>}
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

function RelationRow({
  label,
  target,
  card,
}: {
  label: string;
  target: string;
  card: string;
}) {
  return (
    <>
      <dt>{label}</dt>
      <dd className="muted">
        <span
          className="tile sm"
          style={{ background: tileColor(target), marginRight: 6, verticalAlign: "middle" }}
          aria-hidden
        >
          {initials(target)}
        </span>
        {target} <span className="muted">({card})</span>
      </dd>
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
