"""Vision subpackage: photos -> identified parts.

Prompt 6 contributes the keyless Brickognize HTTP client. Prompt 7
will layer pluggable region detection and interactive scan sessions on
top of it. The public names are re-exported here so callers can simply
write ``from brickfolio.vision import identify_part``.
"""

from brickfolio.vision.brickognize import (
    BrickognizeError,
    PartCandidate,
    identify_part,
)

__all__ = ["BrickognizeError", "PartCandidate", "identify_part"]