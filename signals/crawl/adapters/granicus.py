"""Granicus / Legistar adapter — NOT YET IMPLEMENTED.

Implement discover() against a real Granicus/Legistar calendar page when one is
first added to the registry (Legistar has a documented API; Granicus ViewPublisher
pages are HTML calendars). See the Phase 1 prompt in civica-signals-build-spec.md.
"""

from signals.crawl.adapters.base import BaseAdapter


class GranicusAdapter(BaseAdapter):
    platform = "granicus"

    def discover(self, board):
        raise NotImplementedError(
            "Granicus/Legistar adapter not implemented yet — build it when a "
            "Granicus town enters the registry. See spec Phase 1."
        )
