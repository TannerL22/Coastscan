"""Stable selection parsing for PyDeck, tables and searchable controls."""

from collections.abc import Mapping
from typing import Any

import pandas as pd


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def segment_id_from_pydeck_event(event: object, layer_id: str = "coastline-segments") -> str | None:
    if event is None:
        return None
    event_map = _mapping(event)
    selection = _mapping(event_map.get("selection"))
    if not selection and hasattr(event, "selection"):
        selection = _mapping(event.selection)
    objects = _mapping(selection.get("objects"))
    candidates = objects.get(layer_id, [])
    if not isinstance(candidates, list) or not candidates:
        return None
    first = _mapping(candidates[0])
    properties = _mapping(first.get("properties"))
    value = properties.get("segment_id", first.get("segment_id"))
    return str(value) if value not in (None, "") else None


def segment_id_from_table_event(event: object, visible: pd.DataFrame) -> str | None:
    event_map = _mapping(event)
    selection = _mapping(event_map.get("selection"))
    if not selection and hasattr(event, "selection"):
        selection = _mapping(event.selection)
    rows = selection.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return None
    index = rows[0]
    if not isinstance(index, int) or index < 0 or index >= len(visible):
        return None
    value = visible.iloc[index].get("segment_id")
    return str(value) if value is not None else None


def preserve_selection(selected_segment_id: str | None, visible_ids: set[str]) -> str | None:
    return selected_segment_id if selected_segment_id in visible_ids else None
