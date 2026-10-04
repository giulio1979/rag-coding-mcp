"""Read the metamodel from SQLite into Python dataclasses.

Kept separate from schema construction so tests and tools can observe the
metamodel without pulling in GraphQL dependencies.
"""

from __future__ import annotations

import json
import sqlite3

from rag_coding_mcp.db.metamodel import FieldSpec, InventorySpec, RelationSpec


def read_metamodel(
    conn: sqlite3.Connection,
) -> tuple[list[InventorySpec], list[RelationSpec]]:
    inventories: list[InventorySpec] = []
    for row in conn.execute(
        "SELECT name, description FROM metamodel_inventories ORDER BY name"
    ):
        inv_name = row["name"]
        fields: list[FieldSpec] = []
        for frow in conn.execute(
            "SELECT name, type, enum_values_json, required, description"
            " FROM metamodel_fields WHERE inventory = ? ORDER BY name",
            (inv_name,),
        ):
            enum_values = (
                tuple(json.loads(frow["enum_values_json"]))
                if frow["enum_values_json"]
                else None
            )
            fields.append(
                FieldSpec(
                    name=frow["name"],
                    type=frow["type"],
                    required=bool(frow["required"]),
                    enum_values=enum_values,
                    description=frow["description"],
                )
            )
        inventories.append(
            InventorySpec(name=inv_name, description=row["description"], fields=tuple(fields))
        )

    relations = [
        RelationSpec(
            name=r["name"],
            from_inventory=r["from_inventory"],
            to_inventory=r["to_inventory"],
            cardinality=r["cardinality"],
            description=r["description"],
        )
        for r in conn.execute(
            "SELECT name, from_inventory, to_inventory, cardinality, description"
            " FROM metamodel_relations ORDER BY name"
        )
    ]
    return inventories, relations
