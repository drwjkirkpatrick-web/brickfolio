"""Parse LDraw model text into placed parts (Prompt 8).

LDraw is the de-facto text format for LEGO CAD models. Files are
line-oriented; each line starts with an integer *line type*:

    0   meta command or comment ("0 FILE"/"0 NOFILE" bound sub-files
        inside a multi-part .mpd document)
    1   part placement -- the only line type brickfolio records
    2-5 geometry primitives (line, triangle, quad, optional line);
        ignored in v1 because the inventory only cares about parts

A type-1 line places one part (or, in an .mpd, one sub-model)::

    1 <color> <x> <y> <z> <a b c  d e f  g h i> [<j k l>] <part-file>

LDraw stores the transform as a 4x3 matrix: the first nine values are
the 3x3 rotation recorded on :class:`PlacedPart`.matrix, and the
trailing fourth row (normally "0 0 1") is the homogeneous bottom row,
which we parse and ignore. The classic nine-value form found in real
.ldr files is accepted as well.

Multi-part documents (.mpd) pack several sub-models into one text,
each opened by "0 FILE <name>" and closed by "0 NOFILE". A type-1 line
whose file name matches a sub-model is *flattened*: the sub-model's
placements are copied into the parent with composed transforms (3x3
matrix multiply plus rotated translation add), so the returned
:class:`LDrawModel` is one flat list of parts in root-model
coordinates.

NOTE: the parser is strict about type-1 lines -- malformed input
raises :class:`LDrawParseError` instead of being silently skipped.
WHY: the generator (Prompt 9) feeds these messages back to the LLM so
it can repair its own output; silent skips would let broken models
appear "valid".
"""

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

__all__ = ["PlacedPart", "LDrawModel", "LDrawParseError", "parse_ldraw"]

Vec3 = Tuple[float, float, float]
Matrix3 = Tuple[Vec3, Vec3, Vec3]

_IDENTITY: Matrix3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

# A frame maps one coordinate system into another: a point p written in
# the frame's coordinates appears at R @ p + t in the target system.
_Frame = Tuple[Vec3, Matrix3]

_ROOT_FRAME: _Frame = ((0.0, 0.0, 0.0), _IDENTITY)


class LDrawParseError(ValueError):
    """Raised when text cannot be parsed as an LDraw model.

    WHY a dedicated subclass of ValueError: the generator (Prompt 9)
    catches exactly this to feed the message back to the LLM for a
    repair round, while genuine bugs still surface as other exceptions.
    """


@dataclass(frozen=True)
class PlacedPart:
    """One physical part fixed somewhere in the model.

    position  -- (x, y, z) in LDraw units (LDU), root-model coordinates
    matrix    -- 3x3 rotation as a tuple of three row tuples
    line_num  -- 1-based line of the source text (for error messages)
    """

    line_num: int
    color_id: str
    part_num: str
    position: Vec3
    matrix: Matrix3


@dataclass(frozen=True)
class LDrawModel:
    """A parsed model: a flat list of parts in root-model coordinates."""

    name: str
    parts: List[PlacedPart] = field(default_factory=list)


def parse_ldraw(text: str, name: str = "model") -> LDrawModel:
    """Parse LDraw text (.ldr or .mpd) into one flat LDrawModel.

    ``name`` labels the returned model; it is *not* inferred from
    "0 FILE" headers because the caller knows which file it read.
    """
    # WHY lstrip("\ufeff"): hand-edited files sometimes carry a UTF-8 BOM,
    # which would otherwise glue itself to the first line type token.
    lines = text.lstrip("\ufeff").splitlines()
    sections = _read_sections(lines)
    if sections is None:
        # NOTE: plain .ldr -- no "0 FILE" blocks, so every type-1 line is
        # a part placement and there are no sub-models to resolve.
        parts = list(_flatten(lines, 0, len(lines), {}, _ROOT_FRAME, ()))
        return LDrawModel(name=name, parts=parts)

    # Multi-part document: index sub-models, pick the root, flatten it.
    submodels: Dict[str, Tuple[int, int]] = {}
    for section in sections:
        # NOTE: first definition wins on duplicate names (v1 simplicity).
        submodels.setdefault(_ref_key(section[0]), (section[1], section[2]))

    referenced = set()
    for _name, start, end in sections:
        for i in range(start, end):
            tokens = lines[i].split()
            if len(tokens) >= 2 and tokens[0] == "1":
                referenced.add(_ref_key(tokens[-1]))

    # WHY "first unreferenced": the main model is the one no other
    # sub-model references; MPD writers put it first or last in the wild,
    # so structural position is the robust rule. If everything is
    # referenced (cyclic hand-edit), fall back to the first section.
    root = next(
        (s for s in sections if _ref_key(s[0]) not in referenced), sections[0]
    )
    parts = list(
        _flatten(
            lines,
            root[1],
            root[2],
            submodels,
            _ROOT_FRAME,
            (_ref_key(root[0]),),  # WHY: the root may never re-enter itself
        )
    )
    return LDrawModel(name=name, parts=parts)


def _read_sections(lines: List[str]) -> Optional[List[Tuple[str, int, int]]]:
    """Split ``lines`` into "0 FILE" sections -> [(name, start, end)].

    ``end`` is exclusive. Lines before the first "0 FILE" are ignored
    per the MPD format (they lie outside any sub-file). Returns None
    when the text contains no "0 FILE" line at all -- i.e. it is a
    plain .ldr, and the whole text is one model.
    """
    sections: List[Tuple[str, int, int]] = []
    current: Optional[Tuple[str, int]] = None  # (name, start)
    for idx, raw in enumerate(lines):
        tokens = raw.split()
        if len(tokens) < 2 or tokens[0] != "0":
            continue
        meta = tokens[1].upper()
        if meta == "FILE" and len(tokens) >= 3:
            if current is not None:  # missing NOFILE: section ends here
                sections.append((current[0], current[1], idx))
            # NOTE: rest-of-line, so names may contain spaces (they just
            # cannot be referenced from a type-1 line, which is one token).
            current = (raw.split(None, 2)[2].strip(), idx + 1)
        elif meta == "NOFILE" and current is not None:
            sections.append((current[0], current[1], idx))
            current = None
    if current is not None:
        sections.append((current[0], current[1], len(lines)))
    return sections or None


def _flatten(
    lines: List[str],
    start: int,
    end: int,
    submodels: Dict[str, Tuple[int, int]],
    frame: _Frame,
    visiting: Tuple[str, ...],
) -> Iterator[PlacedPart]:
    """Yield PlacedParts for ``lines[start:end]``, recursing into sub-models.

    ``frame`` maps this range's coordinates into root-model coordinates;
    ``visiting`` is the chain of sub-model names currently being expanded
    (cycle guard).
    """
    for i in range(start, end):
        tokens = lines[i].split()
        if not tokens or tokens[0] != "1":
            continue  # NOTE: line types 0/2/3/4/5 are ignored in v1
        color, pos, rot, ref = _parse_placement(tokens, i + 1)
        key = _ref_key(ref)
        sub = submodels.get(key)
        if sub is None:
            # A real part, or a reference to a file we were not given
            # (external .dat/.ldr). WHY keep it as-is: the validator can
            # then report it as missing from the pile instead of the
            # parser silently dropping geometry.
            world_pos, world_rot = _apply(frame, pos, rot)
            yield PlacedPart(
                line_num=i + 1,
                color_id=color,
                part_num=_part_num(ref),
                position=world_pos,
                matrix=world_rot,
            )
        else:
            if key in visiting:
                raise LDrawParseError(
                    f"circular sub-model reference {ref!r} at line {i + 1}"
                )
            child_frame = _apply(frame, pos, rot)
            yield from _flatten(
                lines, sub[0], sub[1], submodels, child_frame, visiting + (key,)
            )


def _parse_placement(tokens: List[str], line_no: int) -> Tuple[str, Vec3, Matrix3, str]:
    """Parse type-1 tokens -> (color_id, position, rotation, filename)."""
    if len(tokens) < 15:
        raise LDrawParseError(
            f"line {line_no}: malformed part placement, expected "
            f"'1 <color> <x> <y> <z> <matrix> <part>', got {len(tokens)} token(s)"
        )
    try:
        pos = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
        values = [float(tok) for tok in tokens[5:-1]]
    except ValueError:
        raise LDrawParseError(
            f"line {line_no}: non-numeric value in part placement"
        ) from None
    if len(values) == 12:
        # WHY drop the last three: LDraw's transform is 4x3; rows 1-3 are
        # the rotation we record, row 4 (normally "0 0 1") is the
        # homogeneous bottom row with no meaning for part inventory.
        values = values[:9]
    elif len(values) != 9:
        raise LDrawParseError(
            f"line {line_no}: expected 9 or 12 matrix values after the "
            f"position, found {len(values)}"
        )
    rot: Matrix3 = (
        (values[0], values[1], values[2]),
        (values[3], values[4], values[5]),
        (values[6], values[7], values[8]),
    )
    return tokens[1], pos, rot, tokens[-1]


def _apply(frame: _Frame, pos: Vec3, rot: Matrix3) -> _Frame:
    """Place ``pos``/``rot`` into ``frame`` -> (world position, world rotation).

    A point p in the placed system appears at R @ p + t in the frame's
    target, so a nested placement composes as R' = R @ rot and
    t' = R @ pos + t -- the 3x3 multiply plus rotated translation add.
    The same math serves both leaf parts (world position/matrix) and
    sub-model references (the child frame).
    """
    t, rotation = frame
    return _add(_mat_vec(rotation, pos), t), _mat_mul(rotation, rot)


def _mat_vec(m: Matrix3, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _mat_mul(a: Matrix3, b: Matrix3) -> Matrix3:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )  # type: ignore[return-value]


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _ref_key(name: str) -> str:
    """Sub-model names match case-insensitively; accept DOS-style slashes."""
    return name.strip().replace("\\", "/").casefold()


def _part_num(ref: str) -> str:
    """Strip ".dat" so part numbers line up with Rebrickable inventory keys."""
    if ref.casefold().endswith(".dat"):
        return ref[:-4]
    return ref