You are writing a short, factual brief for ONE municipal development "project
story" — a single project tracked across several public town-meeting documents
(agendas, minutes, bid postings). A B2B audience of contractors and real-estate
professionals will read it. You are given the project's events in chronological
order; each was extracted from a public document.

Produce exactly ONE JSON object. No markdown fence, no commentary before or after.

THE HONESTY CONTRACT — this is the whole point of the product, do not break it:

- "what", "status", and "whats_next" must be supported ENTIRELY by the events
  below. Do not introduce any fact, name, number, or date that is not present in
  the events. If the record doesn't say, don't say it.
- "outlook" is the ONLY field where you may look forward or offer a read on
  momentum. Even there, reason only from the trajectory the events show — never
  invent a specific fact, number, name, or commitment. It is shown to users
  explicitly labeled "projection, not from the record", so keep it clearly a
  judgement, not a reported fact.
- NEVER write a dollar amount (a "$" figure) in "what" or "outlook" unless one of
  the events states a dollar_value. If no event states a dollar value, no "$" may
  appear anywhere in those two fields.
- Write plainly. No hype, no filler, no "this exciting project". Past tense for
  what happened; present for where it stands.

Return this exact shape (the system fills est_value, next_date, trades, and
generated_at from the structured record — your values for those are ignored, so
you may leave them as below):

{
  "what": "1-2 sentences: what the project is and where, from the record.",
  "status": "1 sentence: where it stands now — the latest stage and what just happened.",
  "whats_next": "1 sentence: the next concrete step the record points to, or 'No next step stated in the record.'",
  "outlook": "1-2 sentences: your read on momentum or likelihood. Clearly a projection. No new facts, no invented numbers.",
  "trades": [],
  "est_value": null,
  "next_date": null
}

PROJECT: {story_name}{address_clause} — {town}, MA
Latest stage on record: {current_stage}

EVENTS (oldest first):
{events_block}
