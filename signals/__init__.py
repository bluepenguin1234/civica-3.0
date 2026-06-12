"""Civica Signals — Massachusetts municipal-document intelligence subsystem.

A standalone pipeline that lives alongside (and never touches) pipeline/. It
crawls town-government agendas/minutes, extracts structured development events
with Claude, links them into project stories, and publishes a B2B "Signals"
dashboard. See signals/README.md and ../civica-signals-build-spec.md.
"""
