"""Shared python-side types used by the GraphQL layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RequestContext:
    """Request-scoped context passed into every resolver.

    Carries the actor id already resolved by `resolve_actor`, plus a reference
    to the live GraphService so metamodel-mutation resolvers can trigger a
    schema rebuild.
    """

    actor_id: str
    service: Any  # avoid circular import; set to a GraphService at runtime.
