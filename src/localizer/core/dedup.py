"""Cross-source duplicate detection.

Filled in M1. Same property typically appears on 3+ sites; the canonical
listing keeps a single row with N source-references. Fingerprint key:
normalised(adres) + oppervlakte + slaapkamers, with photo-hash fallback.
"""

from __future__ import annotations
