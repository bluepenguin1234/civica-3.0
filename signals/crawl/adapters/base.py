"""Adapter interface for Civica Signals crawlers.

An adapter knows how to DISCOVER document links for one board on one CMS
platform. It does not download anything: it returns DocumentRef descriptors and
the orchestrator (signals/crawl/crawl.py) centralizes downloading, hashing, and
the manifest. Adapters receive a `fetch` callable (url -> HTML text) from the
orchestrator so all network I/O shares one rate limiter, User-Agent, and retry
policy.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentRef:
    """A discovered-but-not-yet-downloaded document."""

    url: str                            # absolute URL to the PDF
    doc_type: str                       # 'agenda' | 'minutes' | 'packet' | 'other'
    guessed_meeting_date: str | None    # ISO 'YYYY-MM-DD', or None if unknown
    filename: str                       # suggested local filename


class BaseAdapter(abc.ABC):
    """Discovers agenda/minutes documents for a board on one CMS platform."""

    platform: str = ""

    def __init__(self, fetch):
        # fetch(url) -> HTML text or None; provided by the orchestrator.
        self.fetch = fetch

    @abc.abstractmethod
    def discover(self, board: dict) -> list[DocumentRef]:
        """Return the documents linked for one registry board entry."""
        raise NotImplementedError
