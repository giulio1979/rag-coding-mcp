"""Dynamic GraphQL schema built from the metamodel at runtime.

Scope of this module (tasks 24, 25, 26):

- Build a `GraphQLObjectType` per inventory with base + declared fields and
  relation traversal fields derived from `metamodel_relations`.
- Query root exposes `metamodel`, per-inventory single lookup and list
  queries with the hybrid-search surface.
- Mutation root exposes per-inventory `createX`/`updateX`/`deleteX` and
  generic `connect(relation, fromId, toId)` / `disconnect(...)`.
- Metamodel mutations and the user slice land in tasks 27 and 28.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from graphql import (
    GraphQLArgument,
    GraphQLBoolean,
    GraphQLEnumType,
    GraphQLField,
    GraphQLFloat,
    GraphQLID,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLInt,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
    print_schema,
)

from rag_coding_mcp.db.metamodel import (
    BASE_COLUMNS,
    FieldSpec,
    InventorySpec,
    RelationSpec,
    table_name_for,
)
from rag_coding_mcp.db.metamodel_ops import (
    add_field,
    create_inventory,
    create_relation_type,
    remove_field,
    remove_relation_type,
)
from rag_coding_mcp.db.metamodel_read import read_metamodel
from rag_coding_mcp.edge_ops import connect_edge, disconnect_edge, list_incoming, list_outgoing
from rag_coding_mcp.embeddings import EmbedderProtocol
from rag_coding_mcp.node_ops import create_node, delete_node, update_node
from rag_coding_mcp.search import fts_search, reciprocal_rank_fusion, vector_search

MATCHED_BY_ENUM = GraphQLEnumType(
    "MatchedBy", {"LEXICAL": "LEXICAL", "SEMANTIC": "SEMANTIC", "BOTH": "BOTH"}
)

_SCALAR_FOR_TYPE: dict[str, Any] = {
    "string": GraphQLString,
    "text": GraphQLString,
    "int": GraphQLInt,
    "bool": GraphQLBoolean,
    "float": GraphQLFloat,
}


@dataclass
class SchemaContext:
    """Request-scoped context passed as `info.context` to every resolver."""

    conn: sqlite3.Connection
    embedder: EmbedderProtocol | None
    actor_id: str
    on_metamodel_change: Any = None  # callable(); invoked by metamodel mutations


def _column_to_graphql(column: str) -> str:
    """`owned_by` -> `ownedBy`, etc."""
    parts = column.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def _field_name_for_inventory(inv_name: str) -> str:
    """`Repo` -> `repo`, `SOP` -> `sop`, `HTTPMethod` -> `httpMethod`."""
    if not inv_name:
        return inv_name
    i = 0
    while i < len(inv_name) and inv_name[i].isupper():
        i += 1
    if i == 0:
        return inv_name
    if i == len(inv_name):
        return inv_name.lower()
    if i > 1 and inv_name[i].islower():
        return inv_name[: i - 1].lower() + inv_name[i - 1 :]
    return inv_name[:i].lower() + inv_name[i:]


def _row_to_node(spec: InventorySpec, row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for col in BASE_COLUMNS:
        out[_column_to_graphql(col)] = row[col]
    # Custom inventory fields keep their declared snake_case name.
    for f in spec.fields:
        out[f.name] = row[f.name]
    return out


def _field_type(field: FieldSpec, enum_namespace: str) -> Any:
    if field.type == "enum":
        values = {v: v for v in (field.enum_values or ())}
        gt = GraphQLEnumType(f"{enum_namespace}_{field.name}", values)
    else:
        gt = _SCALAR_FOR_TYPE.get(field.type, GraphQLString)
    return GraphQLNonNull(gt) if field.required else gt


def _traversal_field_name(relation: str, direction: str) -> str:
    """`contains` outgoing -> `contains`; `contains` incoming -> `containedBy`."""
    parts = relation.split("_")
    camel = parts[0] + "".join(p.title() for p in parts[1:])
    if direction == "out":
        return camel
    return camel + "In"


def _outgoing_resolver(relation: RelationSpec):
    def _resolve(root: dict[str, Any], info: Any) -> list[dict[str, Any]]:
        ctx: SchemaContext = info.context
        ids = list_outgoing(ctx.conn, relation.name, root["id"])
        return _fetch_nodes(ctx.conn, relation.to_inventory, ids)

    return _resolve


def _incoming_resolver(relation: RelationSpec):
    def _resolve(root: dict[str, Any], info: Any) -> list[dict[str, Any]]:
        ctx: SchemaContext = info.context
        ids = list_incoming(ctx.conn, relation.name, root["id"])
        return _fetch_nodes(ctx.conn, relation.from_inventory, ids)

    return _resolve


def _fetch_nodes(
    conn: sqlite3.Connection, inventory: str, ids: list[str]
) -> list[dict[str, Any]]:
    if not ids:
        return []
    spec = _spec_for_inventory(conn, inventory)
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM {table_name_for(inventory)} WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    out: list[dict[str, Any]] = []
    for i in ids:
        if i in by_id:
            node = _row_to_node(spec, by_id[i])
            if node is not None:
                out.append(node)
    return out


def _spec_for_inventory(conn: sqlite3.Connection, name: str) -> InventorySpec:
    inventories, _ = read_metamodel(conn)
    for inv in inventories:
        if inv.name == name:
            return inv
    return InventorySpec(name=name, description="", fields=())


def _build_node_type(
    spec: InventorySpec,
    node_types: dict[str, GraphQLObjectType],
    relations: list[RelationSpec],
) -> GraphQLObjectType:
    def _fields() -> dict[str, GraphQLField]:
        f: dict[str, GraphQLField] = {
            "id": GraphQLField(GraphQLNonNull(GraphQLID)),
            "name": GraphQLField(GraphQLNonNull(GraphQLString)),
            "description": GraphQLField(GraphQLNonNull(GraphQLString)),
            "ownedBy": GraphQLField(GraphQLNonNull(GraphQLString)),
            "updatedBy": GraphQLField(GraphQLNonNull(GraphQLString)),
            "createdAt": GraphQLField(GraphQLNonNull(GraphQLString)),
            "updatedAt": GraphQLField(GraphQLNonNull(GraphQLString)),
        }
        for fs in spec.fields:
            f[fs.name] = GraphQLField(_field_type(fs, spec.name))
        for rel in relations:
            if rel.from_inventory == spec.name:
                target = node_types[rel.to_inventory]
                f[_traversal_field_name(rel.name, "out")] = GraphQLField(
                    GraphQLNonNull(GraphQLList(GraphQLNonNull(target))),
                    resolve=_outgoing_resolver(rel),
                )
            if rel.to_inventory == spec.name:
                source = node_types[rel.from_inventory]
                f[_traversal_field_name(rel.name, "in")] = GraphQLField(
                    GraphQLNonNull(GraphQLList(GraphQLNonNull(source))),
                    resolve=_incoming_resolver(rel),
                )
        return f

    return GraphQLObjectType(spec.name, _fields, description=spec.description or None)


def _build_result_type(spec: InventorySpec, node_type: GraphQLObjectType) -> GraphQLObjectType:
    return GraphQLObjectType(
        f"{spec.name}Result",
        {
            "node": GraphQLField(GraphQLNonNull(node_type)),
            "score": GraphQLField(GraphQLNonNull(GraphQLFloat)),
            "matchedBy": GraphQLField(GraphQLNonNull(MATCHED_BY_ENUM)),
        },
    )


def _lookup_resolver(spec: InventorySpec):
    def _resolve(_root: Any, info: Any, id: str) -> dict[str, Any] | None:
        ctx: SchemaContext = info.context
        row = ctx.conn.execute(
            f"SELECT * FROM {table_name_for(spec.name)} WHERE id = ?", (id,)
        ).fetchone()
        return _row_to_node(spec, row)

    return _resolve


def _list_resolver(spec: InventorySpec):
    def _resolve(
        _root: Any,
        info: Any,
        text: str | None = None,
        similarTo: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        ctx: SchemaContext = info.context
        results = _hybrid_search(
            conn=ctx.conn,
            embedder=ctx.embedder,
            spec=spec,
            text=text,
            similar_to=similarTo,
            limit=limit,
            offset=offset,
        )
        return results

    return _resolve


def _hybrid_search(
    conn: sqlite3.Connection,
    embedder: EmbedderProtocol | None,
    spec: InventorySpec,
    text: str | None,
    similar_to: str | None,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    table = table_name_for(spec.name)
    use_lexical = bool(text and text.strip())
    use_semantic = bool(similar_to and similar_to.strip())

    if not use_lexical and not use_semantic:
        rows = conn.execute(
            f"SELECT * FROM {table} ORDER BY created_at LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [
            {
                "node": _row_to_node(spec, r),
                "score": 1.0,
                "matchedBy": "LEXICAL",  # not a search; tag arbitrarily but consistently
            }
            for r in rows
        ]

    lexical: list[tuple[str, float]] = []
    semantic: list[tuple[str, float]] = []
    if use_lexical:
        lexical = fts_search(conn, spec.name, text or "", limit=limit * 4)
    if use_semantic:
        if embedder is None:
            raise RuntimeError("similarTo requested but no embedder configured")
        semantic = vector_search(conn, embedder, spec.name, similar_to or "", limit=limit * 4)

    fused = reciprocal_rank_fusion(lexical, semantic, limit=limit + offset)[offset : offset + limit]
    if not fused:
        return []

    ids = [r.node_id for r in fused]
    placeholders = ",".join("?" for _ in ids)
    rows = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT * FROM {table} WHERE id IN ({placeholders})", ids
        ).fetchall()
    }
    out: list[dict[str, Any]] = []
    for r in fused:
        if r.node_id not in rows:
            continue
        out.append(
            {
                "node": _row_to_node(spec, rows[r.node_id]),
                "score": r.score,
                "matchedBy": r.matched_by,
            }
        )
    return out


# --- user slice -----------------------------------------------------------

UserType = GraphQLObjectType(
    "User",
    {
        "id": GraphQLField(GraphQLNonNull(GraphQLID)),
        "name": GraphQLField(GraphQLNonNull(GraphQLString)),
        "isAdmin": GraphQLField(GraphQLNonNull(GraphQLBoolean)),
        "createdAt": GraphQLField(GraphQLNonNull(GraphQLString)),
    },
)


def _users_resolver(_root: Any, info: Any) -> list[dict[str, Any]]:
    from rag_coding_mcp.user_ops import list_users

    ctx: SchemaContext = info.context
    return list_users(ctx.conn)


def _create_user_resolver(
    _root: Any, info: Any, name: str, isAdmin: bool = False
) -> dict[str, Any]:
    from rag_coding_mcp.user_ops import create_user, list_users

    ctx: SchemaContext = info.context
    uid = create_user(ctx.conn, name, is_admin=isAdmin)
    for u in list_users(ctx.conn):
        if u["id"] == uid:
            return u
    raise RuntimeError("created user not found")


def _rename_user_resolver(_root: Any, info: Any, id: str, name: str) -> dict[str, Any]:
    from rag_coding_mcp.user_ops import list_users, rename_user

    ctx: SchemaContext = info.context
    rename_user(ctx.conn, id, name)
    for u in list_users(ctx.conn):
        if u["id"] == id:
            return u
    raise RuntimeError("user vanished after rename")


def _set_user_admin_resolver(
    _root: Any, info: Any, id: str, isAdmin: bool
) -> dict[str, Any]:
    from rag_coding_mcp.user_ops import list_users, set_user_admin

    ctx: SchemaContext = info.context
    set_user_admin(ctx.conn, id, isAdmin)
    for u in list_users(ctx.conn):
        if u["id"] == id:
            return u
    raise RuntimeError("user vanished after setUserAdmin")


# --- metamodel introspection types ----------------------------------------

MetamodelFieldType = GraphQLObjectType(
    "MetamodelField",
    {
        "name": GraphQLField(GraphQLNonNull(GraphQLString)),
        "type": GraphQLField(GraphQLNonNull(GraphQLString)),
        "required": GraphQLField(GraphQLNonNull(GraphQLBoolean)),
        "enumValues": GraphQLField(GraphQLList(GraphQLNonNull(GraphQLString))),
        "description": GraphQLField(GraphQLNonNull(GraphQLString)),
    },
)

MetamodelInventoryType = GraphQLObjectType(
    "MetamodelInventory",
    {
        "name": GraphQLField(GraphQLNonNull(GraphQLString)),
        "description": GraphQLField(GraphQLNonNull(GraphQLString)),
        "fields": GraphQLField(GraphQLNonNull(GraphQLList(GraphQLNonNull(MetamodelFieldType)))),
    },
)

MetamodelRelationType = GraphQLObjectType(
    "MetamodelRelation",
    {
        "name": GraphQLField(GraphQLNonNull(GraphQLString)),
        "fromInventory": GraphQLField(GraphQLNonNull(GraphQLString)),
        "toInventory": GraphQLField(GraphQLNonNull(GraphQLString)),
        "cardinality": GraphQLField(GraphQLNonNull(GraphQLString)),
        "description": GraphQLField(GraphQLNonNull(GraphQLString)),
    },
)

MetamodelDocType = GraphQLObjectType(
    "MetamodelDoc",
    {
        "inventories": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(MetamodelInventoryType)))
        ),
        "relations": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(MetamodelRelationType)))
        ),
        "embeddingModel": GraphQLField(GraphQLString),
        "embeddingDim": GraphQLField(GraphQLInt),
        "sdl": GraphQLField(GraphQLNonNull(GraphQLString)),
    },
)


def _metamodel_resolver(root_schema_holder: list[GraphQLSchema]):
    def _resolve(_root: Any, info: Any) -> dict[str, Any]:
        ctx: SchemaContext = info.context
        inventories, relations = read_metamodel(ctx.conn)
        return {
            "inventories": [
                {
                    "name": inv.name,
                    "description": inv.description,
                    "fields": [
                        {
                            "name": f.name,
                            "type": f.type,
                            "required": f.required,
                            "enumValues": list(f.enum_values) if f.enum_values else None,
                            "description": f.description,
                        }
                        for f in inv.fields
                    ],
                }
                for inv in inventories
            ],
            "relations": [
                {
                    "name": r.name,
                    "fromInventory": r.from_inventory,
                    "toInventory": r.to_inventory,
                    "cardinality": r.cardinality,
                    "description": r.description,
                }
                for r in relations
            ],
            "embeddingModel": ctx.embedder.model_name if ctx.embedder else None,
            "embeddingDim": ctx.embedder.dim if ctx.embedder else None,
            "sdl": print_schema(root_schema_holder[0]),
        }

    return _resolve


# --- mutations ------------------------------------------------------------


def _input_field_type(field: FieldSpec, enum_namespace: str) -> Any:
    if field.type == "enum":
        values = {v: v for v in (field.enum_values or ())}
        return GraphQLEnumType(f"{enum_namespace}_{field.name}_Input", values)
    return _SCALAR_FOR_TYPE.get(field.type, GraphQLString)


def _build_create_input(spec: InventorySpec) -> GraphQLInputObjectType:
    fields: dict[str, GraphQLInputField] = {
        "name": GraphQLInputField(GraphQLNonNull(GraphQLString)),
        "description": GraphQLInputField(GraphQLString, default_value=""),
    }
    for fs in spec.fields:
        gt = _input_field_type(fs, spec.name + "_Create")
        fields[fs.name] = GraphQLInputField(GraphQLNonNull(gt) if fs.required else gt)
    return GraphQLInputObjectType(f"Create{spec.name}Input", fields)


def _build_update_input(spec: InventorySpec) -> GraphQLInputObjectType:
    fields: dict[str, GraphQLInputField] = {
        "name": GraphQLInputField(GraphQLString),
        "description": GraphQLInputField(GraphQLString),
    }
    for fs in spec.fields:
        gt = _input_field_type(fs, spec.name + "_Update")
        fields[fs.name] = GraphQLInputField(gt)
    return GraphQLInputObjectType(f"Update{spec.name}Input", fields)


def _declared_from_input(spec: InventorySpec, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {f.name for f in spec.fields}
    return {k: v for k, v in data.items() if k in allowed}


def _create_resolver(spec: InventorySpec, node_type_name: str):
    def _resolve(_root: Any, info: Any, input: dict[str, Any]) -> dict[str, Any] | None:
        ctx: SchemaContext = info.context
        name = input["name"]
        description = input.get("description", "") or ""
        fields = _declared_from_input(spec, input)
        node_id = create_node(
            ctx.conn,
            spec,
            name=name,
            description=description,
            actor_id=ctx.actor_id,
            fields=fields,
            embedder=ctx.embedder,
        )
        row = ctx.conn.execute(
            f"SELECT * FROM {table_name_for(spec.name)} WHERE id = ?", (node_id,)
        ).fetchone()
        return _row_to_node(spec, row)

    _resolve.__name__ = f"create_{node_type_name}"
    return _resolve


def _update_resolver(spec: InventorySpec, node_type_name: str):
    def _resolve(
        _root: Any, info: Any, id: str, input: dict[str, Any]
    ) -> dict[str, Any] | None:
        ctx: SchemaContext = info.context
        update_node(
            ctx.conn,
            spec,
            id,
            actor_id=ctx.actor_id,
            name=input.get("name"),
            description=input.get("description"),
            fields=_declared_from_input(spec, input),
            embedder=ctx.embedder,
        )
        row = ctx.conn.execute(
            f"SELECT * FROM {table_name_for(spec.name)} WHERE id = ?", (id,)
        ).fetchone()
        return _row_to_node(spec, row)

    _resolve.__name__ = f"update_{node_type_name}"
    return _resolve


def _delete_resolver(spec: InventorySpec):
    def _resolve(_root: Any, info: Any, id: str) -> bool:
        ctx: SchemaContext = info.context
        delete_node(ctx.conn, spec, id)
        return True

    return _resolve


def _connect_resolver(_root: Any, info: Any, relation: str, fromId: str, toId: str) -> bool:
    ctx: SchemaContext = info.context
    connect_edge(ctx.conn, relation, fromId, toId, actor_id=ctx.actor_id)
    return True


def _disconnect_resolver(_root: Any, info: Any, relation: str, fromId: str, toId: str) -> bool:
    ctx: SchemaContext = info.context
    disconnect_edge(ctx.conn, relation, fromId, toId)
    return True


# --- metamodel mutations --------------------------------------------------


FieldInputType = GraphQLInputObjectType(
    "FieldInput",
    {
        "name": GraphQLInputField(GraphQLNonNull(GraphQLString)),
        "type": GraphQLInputField(GraphQLNonNull(GraphQLString)),
        "enumValues": GraphQLInputField(GraphQLList(GraphQLNonNull(GraphQLString))),
        "required": GraphQLInputField(GraphQLBoolean, default_value=False),
        "description": GraphQLInputField(GraphQLString, default_value=""),
    },
)


def _field_input_to_spec(data: dict[str, Any]) -> FieldSpec:
    enum_vals = data.get("enumValues")
    return FieldSpec(
        name=data["name"],
        type=data["type"],
        enum_values=tuple(enum_vals) if enum_vals else None,
        required=bool(data.get("required", False)),
        description=data.get("description") or "",
    )


def _notify(ctx: SchemaContext) -> None:
    if ctx.on_metamodel_change is not None:
        ctx.on_metamodel_change()


def _create_inventory_resolver(
    _root: Any,
    info: Any,
    name: str,
    fields: list[dict[str, Any]] | None = None,
    description: str = "",
) -> bool:
    ctx: SchemaContext = info.context
    specs = tuple(_field_input_to_spec(f) for f in (fields or []))
    create_inventory(
        ctx.conn, InventorySpec(name=name, description=description, fields=specs)
    )
    _notify(ctx)
    return True


def _add_field_resolver(
    _root: Any, info: Any, inventory: str, field: dict[str, Any]
) -> bool:
    ctx: SchemaContext = info.context
    add_field(ctx.conn, inventory, _field_input_to_spec(field))
    _notify(ctx)
    return True


def _remove_field_resolver(_root: Any, info: Any, inventory: str, name: str) -> bool:
    ctx: SchemaContext = info.context
    remove_field(ctx.conn, inventory, name)
    _notify(ctx)
    return True


def _create_relation_type_resolver(
    _root: Any,
    info: Any,
    name: str,
    fromInventory: str,
    toInventory: str,
    cardinality: str = "many",
    description: str = "",
) -> bool:
    ctx: SchemaContext = info.context
    create_relation_type(
        ctx.conn,
        RelationSpec(
            name=name,
            from_inventory=fromInventory,
            to_inventory=toInventory,
            cardinality=cardinality,
            description=description,
        ),
    )
    _notify(ctx)
    return True


def _remove_relation_type_resolver(_root: Any, info: Any, name: str) -> bool:
    ctx: SchemaContext = info.context
    remove_relation_type(ctx.conn, name)
    _notify(ctx)
    return True


# --- schema assembly ------------------------------------------------------


def build_schema(conn: sqlite3.Connection) -> GraphQLSchema:
    """Produce a GraphQLSchema reflecting the current metamodel."""
    inventories, relations = read_metamodel(conn)

    node_types: dict[str, GraphQLObjectType] = {}
    for inv in inventories:
        node_types[inv.name] = _build_node_type(inv, node_types, relations)
    result_types = {
        inv.name: _build_result_type(inv, node_types[inv.name]) for inv in inventories
    }

    query_fields: dict[str, GraphQLField] = {}
    schema_holder: list[GraphQLSchema] = []

    query_fields["metamodel"] = GraphQLField(
        GraphQLNonNull(MetamodelDocType),
        resolve=_metamodel_resolver(schema_holder),
    )

    query_fields["users"] = GraphQLField(
        GraphQLNonNull(GraphQLList(GraphQLNonNull(UserType))),
        resolve=_users_resolver,
    )

    for inv in inventories:
        inv_lower = _field_name_for_inventory(inv.name)
        query_fields[inv_lower] = GraphQLField(
            node_types[inv.name],
            args={"id": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=_lookup_resolver(inv),
        )
        query_fields[inv_lower + "s"] = GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(result_types[inv.name]))),
            args={
                "text": GraphQLArgument(GraphQLString),
                "similarTo": GraphQLArgument(GraphQLString),
                "limit": GraphQLArgument(GraphQLInt, default_value=50),
                "offset": GraphQLArgument(GraphQLInt, default_value=0),
            },
            resolve=_list_resolver(inv),
        )

    mutation_fields: dict[str, GraphQLField] = {}
    for inv in inventories:
        create_input = _build_create_input(inv)
        update_input = _build_update_input(inv)
        mutation_fields[f"create{inv.name}"] = GraphQLField(
            GraphQLNonNull(node_types[inv.name]),
            args={"input": GraphQLArgument(GraphQLNonNull(create_input))},
            resolve=_create_resolver(inv, inv.name),
        )
        mutation_fields[f"update{inv.name}"] = GraphQLField(
            GraphQLNonNull(node_types[inv.name]),
            args={
                "id": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "input": GraphQLArgument(GraphQLNonNull(update_input)),
            },
            resolve=_update_resolver(inv, inv.name),
        )
        mutation_fields[f"delete{inv.name}"] = GraphQLField(
            GraphQLNonNull(GraphQLBoolean),
            args={"id": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=_delete_resolver(inv),
        )

    mutation_fields["connect"] = GraphQLField(
        GraphQLNonNull(GraphQLBoolean),
        args={
            "relation": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "fromId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
            "toId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
        },
        resolve=_connect_resolver,
    )
    mutation_fields["disconnect"] = GraphQLField(
        GraphQLNonNull(GraphQLBoolean),
        args={
            "relation": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "fromId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
            "toId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
        },
        resolve=_disconnect_resolver,
    )

    mutation_fields["createInventory"] = GraphQLField(
        GraphQLNonNull(GraphQLBoolean),
        args={
            "name": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "fields": GraphQLArgument(GraphQLList(GraphQLNonNull(FieldInputType))),
            "description": GraphQLArgument(GraphQLString, default_value=""),
        },
        resolve=_create_inventory_resolver,
    )
    mutation_fields["addField"] = GraphQLField(
        GraphQLNonNull(GraphQLBoolean),
        args={
            "inventory": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "field": GraphQLArgument(GraphQLNonNull(FieldInputType)),
        },
        resolve=_add_field_resolver,
    )
    mutation_fields["removeField"] = GraphQLField(
        GraphQLNonNull(GraphQLBoolean),
        args={
            "inventory": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "name": GraphQLArgument(GraphQLNonNull(GraphQLString)),
        },
        resolve=_remove_field_resolver,
    )
    mutation_fields["createRelationType"] = GraphQLField(
        GraphQLNonNull(GraphQLBoolean),
        args={
            "name": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "fromInventory": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "toInventory": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "cardinality": GraphQLArgument(GraphQLString, default_value="many"),
            "description": GraphQLArgument(GraphQLString, default_value=""),
        },
        resolve=_create_relation_type_resolver,
    )
    mutation_fields["removeRelationType"] = GraphQLField(
        GraphQLNonNull(GraphQLBoolean),
        args={"name": GraphQLArgument(GraphQLNonNull(GraphQLString))},
        resolve=_remove_relation_type_resolver,
    )

    mutation_fields["createUser"] = GraphQLField(
        GraphQLNonNull(UserType),
        args={
            "name": GraphQLArgument(GraphQLNonNull(GraphQLString)),
            "isAdmin": GraphQLArgument(GraphQLBoolean, default_value=False),
        },
        resolve=_create_user_resolver,
    )
    mutation_fields["renameUser"] = GraphQLField(
        GraphQLNonNull(UserType),
        args={
            "id": GraphQLArgument(GraphQLNonNull(GraphQLID)),
            "name": GraphQLArgument(GraphQLNonNull(GraphQLString)),
        },
        resolve=_rename_user_resolver,
    )
    mutation_fields["setUserAdmin"] = GraphQLField(
        GraphQLNonNull(UserType),
        args={
            "id": GraphQLArgument(GraphQLNonNull(GraphQLID)),
            "isAdmin": GraphQLArgument(GraphQLNonNull(GraphQLBoolean)),
        },
        resolve=_set_user_admin_resolver,
    )

    schema = GraphQLSchema(
        query=GraphQLObjectType("Query", query_fields),
        mutation=GraphQLObjectType("Mutation", mutation_fields),
    )
    schema_holder.append(schema)
    return schema


def print_sdl(schema: GraphQLSchema) -> str:
    return print_schema(schema)
