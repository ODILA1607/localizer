"""Zimmo connector. Pilot bron, eerst geïmplementeerd (M3).

Strategy: bot-protection laag 1 (headers + cookies). Zimmo levert
meestal nette JSON-LD in de HTML — parse() richt zich daarop.
"""

from __future__ import annotations
