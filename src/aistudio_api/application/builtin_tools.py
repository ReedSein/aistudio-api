"""Resolve configured builtin tools before encoding the wire representation.

The existing model-family compatibility rules remain authoritative here.
Unverified ListModels capability labels are observational metadata, so absence
of a label cannot silently remove a tool from a native request.
"""

from __future__ import annotations


def resolve_builtin_tool_names(
    tool_names: list[str] | tuple[str, ...],
    *,
    model: str,
    is_image_model: bool = False,
    drop_unsupported: bool = False,
) -> list[str]:
    if is_image_model:
        allowed = {"google_search", "image_search", "google_search_and_image_search"}
    elif model.removeprefix("models/").lower().startswith("gemini-"):
        allowed = {"google_search", "code_execution", "google_maps", "url_context"}
    else:
        allowed = {"google_search", "code_execution"}
    result = []
    for raw_name in tool_names:
        name = raw_name.strip().lower()
        if not name:
            continue
        if is_image_model:
            if name in ("image_google_search",):
                name = "google_search"
            elif name in ("google_image_search",):
                name = "image_search"
            elif name == "image_google_search_and_image_search":
                name = "google_search_and_image_search"
        if name not in allowed:
            if drop_unsupported:
                continue
            raise ValueError(f"Tool {raw_name!r} is not allowed for model {model}")
        result.append(name)
    return result
