"""LDraw model reading and inventory validation (Prompt 8).

Two pure modules, no I/O (text goes in, dataclasses come out):

- ``brickfolio.ldraw.parser`` turns LDraw text -- plain ``.ldr`` files or
  ``.mpd`` multi-part documents -- into a flat
  :class:`~brickfolio.ldraw.parser.LDrawModel`, with sub-model references
  flattened into composed (position, matrix) placements.
- ``brickfolio.ldraw.validator`` proves a model is buildable from the
  user's parts (exact/shape modes) and checks placements for AABB
  collisions using a small default bounding-box table.

The inventory-constrained generator (Prompt 9) builds on both: parse
LLM output, validate against the pile, feed errors back for repair.
"""

from brickfolio.ldraw.parser import (
    LDrawModel,
    LDrawParseError,
    PlacedPart,
    parse_ldraw,
)
from brickfolio.ldraw.validator import (
    DEFAULT_BBOXES,
    ValidationReport,
    check_collisions,
    validate_against_inventory,
)

__all__ = [
    "DEFAULT_BBOXES",
    "LDrawModel",
    "LDrawParseError",
    "PlacedPart",
    "ValidationReport",
    "check_collisions",
    "parse_ldraw",
    "validate_against_inventory",
]