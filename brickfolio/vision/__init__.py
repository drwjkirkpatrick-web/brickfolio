"""Vision subpackage: photos -> identified parts.

Prompt 6 contributes the keyless Brickognize HTTP client; Prompt 7 layers
pluggable region detection and review sessions on top of it. The public
names are re-exported here so callers can simply write
``from brickfolio.vision import identify_part``.
"""

from brickfolio.vision.brickognize import (
    BrickognizeError,
    PartCandidate,
    identify_part,
)
from brickfolio.vision.detector import (
    Detector,
    DetectorError,
    ManualRegionsDetector,
    Region,
    StaticDetector,
)
from brickfolio.vision.session import (
    DEFAULT_COLOR_ID,
    PhotoCandidates,
    ScanSession,
)

__all__ = [
    "BrickognizeError",
    "PartCandidate",
    "identify_part",
    "Detector",
    "DetectorError",
    "ManualRegionsDetector",
    "Region",
    "StaticDetector",
    "DEFAULT_COLOR_ID",
    "PhotoCandidates",
    "ScanSession",
]