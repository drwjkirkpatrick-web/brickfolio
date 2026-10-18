"""Inventory-constrained LDraw model generation (Prompt 9).

The feature Brickit doesn't have: let an LLM *design* something, then
prove -- with the Prompt-8 parser and validator, not with hope -- that
the design is buildable from the pile the user actually owns.

One generation run is a strict ask/repair loop::

    build_system_prompt(inventory)   "use ONLY these parts" + listing
                    |
                    v
        LLM reply --> parse_ldraw --> validate_against_inventory
                    ^                                  |
                    +----- errors fed back verbatim ----+

Every failed round appends the validator's own error strings -- they
carry part, color, needed and have precisely so an LLM can act on them
-- to the conversation, and the next round must repair them.  A run ends
the first time a model validates (``valid=True``), or when the round
budget is spent, or when the LLM transport itself fails (both
``valid=False`` with ``errors`` explaining why).

WHY the LLM is never trusted: parsing and validation run after *every*
reply, so an unusable model can never be returned as valid, no matter
how confident the model sounded -- and any model that does validate is
buildable by construction, because the check is the same one the
recommender runs on human-designed sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from brickfolio.llm import LLMError
from brickfolio.ldraw.parser import LDrawModel, LDrawParseError, parse_ldraw
from brickfolio.ldraw.validator import MODES, validate_against_inventory
from brickfolio.models import InventoryItem

__all__ = ["GenerationResult", "build_system_prompt", "generate_build"]


@dataclass(frozen=True)
class GenerationResult:
    """Outcome of one :func:`generate_build` run.

    model  -- the last successfully parsed model, even when invalid
              (None when the final round did not parse at all)
    rounds -- LLM round-trips actually used, 1..max_rounds
    valid  -- True iff the final model validated against the inventory
    usage  -- (part_num, color_id, quantity) tuples the model uses,
              aggregated and sorted for a stable report
    errors -- the *final* round's problems: validator errors, a parse
              problem, or the transport failure message.  Never the
              history of every round -- a defect fixed in round 2 is
              not an error of the result.

    NOTE: ``valid`` is recomputed from ``errors`` in ``__post_init__``,
    house style (see ValidationReport): a result can never claim
    success while listing problems.
    """

    model: Optional[LDrawModel] = None
    rounds: int = 0
    valid: bool = False
    usage: List[Tuple[str, str, int]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid", not self.errors)


def build_system_prompt(inventory: Iterable[InventoryItem]) -> str:
    """The system prompt: design LDraw models using ONLY these parts.

    The inventory is aggregated per (part, color) first -- duplicate
    lines and per-color splits must not leak into the prompt as
    contradictions -- and listed ``part color: quantity`` (one line per
    key, deterministic order) so the constraint is greppable by both
    the LLM and the tests.
    """
    lines = [
        "You are a LEGO designer. You design small LDraw models that can",
        "be built from one fixed, physical parts inventory.",
        "",
        "Hard constraints:",
        "- Use ONLY the parts listed below -- no other part numbers, ever.",
        "- Never use more copies of a part in a color than the quantity",
        "  listed: each placed part is one physical brick, so the number of",
        "  placement lines for a part is the quantity you consume.",
        "",
        "Available parts, as 'part color: quantity':",
    ]
    for (part_num, color_id), quantity in sorted(_aggregate(inventory).items()):
        lines.append(f"{part_num} color {color_id}: {quantity}")
    lines.extend(
        [
            "",
            "Output format -- a plain LDraw file, one line per placed part:",
            "  1 <color> <x> <y> <z> <matrix values> <part>.dat",
            "The matrix is LDraw's transform: 9 values (the 3x3 rotation) or",
            "the full 12-value form. Coordinates are LDraw units (LDU): one",
            "stud pitch is 20 LDU, a brick is 24 LDU tall, a plate 8 LDU.",
            "Colors are LDraw color numbers, exactly as listed above.",
            "",
            "Reply with the complete LDraw file only -- no prose, no markdown.",
        ]
    )
    return "\n".join(lines)


def generate_build(
    inventory: Iterable[InventoryItem],
    theme: str,
    client: Any,
    max_rounds: int = 3,
    mode: str = "exact",
) -> GenerationResult:
    """Design an LDraw model for ``theme`` constrained to ``inventory``.

    Args:
        inventory: the user's parts (duplicate lines are fine -- the
            prompt and the validator both aggregate first).
        theme: free-text design brief ("a small house with a garden").
        client: anything with ``chat(messages) -> str`` -- in production
            a :class:`brickfolio.llm.ChatClient`, in tests a scripted fake.
        max_rounds: LLM round-trip budget, including the first attempt.
        mode: validator mode -- ``"exact"`` (default) enforces the listed
            colors; ``"shape"`` accepts any color of an owned part and
            only warns on substitution.  Optional extension of the
            Prompt-9 signature that keeps the Prompt-8 modes reachable.

    Returns:
        A :class:`GenerationResult`; see the dataclass for semantics.
        Transport failures are *not* raised -- they end the run as
        ``valid=False`` with the reason in ``errors``, because the CLI
        prints a result either way.  Anything else that raises is a bug.

    Raises:
        ValueError: ``mode`` is unknown or ``max_rounds`` is below 1.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected 'exact' or 'shape'")
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be at least 1, got {max_rounds}")

    # WHY list() once: inventory is an Iterable consumed twice (system
    # prompt + every validation round); a bare generator would be
    # exhausted by the first consumer and silently validate against
    # nothing.
    stock = list(inventory)

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(stock)},
        {"role": "user", "content": _first_ask(theme, mode)},
    ]

    model: Optional[LDrawModel] = None
    errors: List[str] = []
    rounds = 0

    for _ in range(max_rounds):
        rounds += 1
        try:
            reply = client.chat(messages)
        except LLMError as exc:
            # WHY stop instead of retrying: a dead server fails every
            # remaining round identically; the caller gets the reason now.
            errors = [f"LLM call failed: {exc}"]
            break

        # The conversation keeps its full history -- the LLM can only
        # fix its own output if it can still see it.
        messages.append({"role": "assistant", "content": reply})

        candidate, problem = _parse_reply(reply, name=theme or "generated")
        if candidate is None or problem is not None:
            # Exactly one of the two is set (see _parse_reply); the
            # ``or`` is for the type checker, not a reachable path.
            errors = [problem or "the model could not be parsed"]
            messages.append({"role": "user", "content": _repair_ask(errors, [])})
            continue
        model = candidate
        report = validate_against_inventory(model, stock, mode)
        if report.ok:
            errors = []
            break
        # NOTE: warnings ride along as hints -- they name substitute
        # colors, which is exactly what the next round needs to know.
        errors = list(report.errors)
        messages.append(
            {"role": "user", "content": _repair_ask(report.errors, report.warnings)}
        )

    return GenerationResult(
        model=model,
        rounds=rounds,
        valid=not errors,  # recomputed in __post_init__; kept for readability
        usage=_usage_of(model),
        errors=errors,
    )


# --- private helpers ---------------------------------------------------------


def _aggregate(inventory: Iterable[InventoryItem]) -> Dict[Tuple[str, str], int]:
    """(part_num, color_id) -> total quantity across duplicate lines."""
    stock: Dict[Tuple[str, str], int] = {}
    for item in inventory:
        key = (item.part_num, item.color_id)
        stock[key] = stock.get(key, 0) + item.quantity
    return stock


def _parse_reply(reply: str, name: str) -> Tuple[Optional[LDrawModel], Optional[str]]:
    """Parse one LLM reply -> (model, problem); exactly one is not None."""
    try:
        model = parse_ldraw(_extract_ldraw(reply), name=name)
    except LDrawParseError as exc:
        # The parser's message is LLM-actionable (it names the line);
        # keep it verbatim inside a sentence the repair prompt can quote.
        return None, f"the reply is not valid LDraw text: {exc}"
    if not model.parts:
        # WHY treat "parses but has no parts" as a problem: prose-only
        # replies parse successfully into an empty model, and an empty
        # model would validate trivially -- the one way the loop could
        # return "valid" with nothing built.
        return None, (
            "the model contains no part placements "
            "(no type-1 part lines were found)"
        )
    return model, None


def _extract_ldraw(reply: str) -> str:
    """Pull the LDraw text out of one LLM reply.

    WHY: models habitually wrap file output in markdown fences or add a
    chatty intro no matter what the prompt says.  If a fence exists its
    contents win (the first fence -- a second block is usually
    commentary); otherwise the reply is used as-is, where stray prose
    costs nothing because the parser ignores non-type-1 lines.
    """
    if "```" not in reply:
        return reply
    opening = reply.index("```")
    newline = reply.find("\n", opening)
    if newline == -1:
        return reply  # a fence marker with nothing after it: not extractable
    body = reply[newline + 1 :]
    closing = body.find("```")
    if closing != -1:
        body = body[:closing]
    return body.strip()


def _first_ask(theme: str, mode: str) -> str:
    """The round-1 design brief."""
    color_rule = (
        "Use each part in exactly the color it is listed in."
        if mode == "exact"
        else "Prefer the listed colors; a different color of an owned part"
        " is acceptable."
    )
    return (
        f"Design a LEGO model on this theme: {theme!r}. {color_rule} "
        "Keep it small and physically sensible: bricks sit flush on the "
        "20-LDU grid and must not overlap. Reply with the complete LDraw "
        "file only."
    )


def _repair_ask(errors: List[str], warnings: List[str]) -> str:
    """The correction-round prompt: quote the problems, ask for the fix."""
    lines = ["That model cannot be built from the inventory. Problems:"]
    lines.extend(f"- {error}" for error in errors)
    if warnings:
        lines.append("Hints from the validator:")
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append(
        "Fix every problem and reply with the complete corrected LDraw "
        "file (the full file, not just the changed lines)."
    )
    return "\n".join(lines)


def _usage_of(model: Optional[LDrawModel]) -> List[Tuple[str, str, int]]:
    """(part, color, qty) the model actually uses, sorted for stable reports."""
    if model is None:
        return []
    counts: Dict[Tuple[str, str], int] = {}
    for placed in model.parts:
        key = (placed.part_num, placed.color_id)
        counts[key] = counts.get(key, 0) + 1
    return [(part, color, qty) for (part, color), qty in sorted(counts.items())]