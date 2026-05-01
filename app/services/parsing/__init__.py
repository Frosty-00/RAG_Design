"""Document parsing layer.

Public entry: `parse_file(path)` in `router` returns a list of LlamaIndex
`Document` objects with normalized metadata.
"""
from app.services.parsing.router import parse_file

__all__ = ["parse_file"]
