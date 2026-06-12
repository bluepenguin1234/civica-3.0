"""CivicClerk portal adapter — NOT YET IMPLEMENTED.

CivicClerk exposes a JSON API behind the portal page. Implement discover()
against a real CivicClerk town when one is first added to the registry: inspect
the portal's network calls, prefer the stable JSON API, fall back to HTML. See
the Phase 1 prompt in civica-signals-build-spec.md.
"""

from signals.crawl.adapters.base import BaseAdapter


class CivicClerkAdapter(BaseAdapter):
    platform = "civicclerk"

    def discover(self, board):
        raise NotImplementedError(
            "CivicClerk adapter not implemented yet — build it when a CivicClerk "
            "town enters the registry (inspect its JSON API first). See spec Phase 1."
        )
