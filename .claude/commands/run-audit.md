---
description: Run a full live audit for a client CID and render the deck, with QA
---

Run a complete audit for the client given in $ARGUMENTS (expects: client name,
CID like 123-456-7890, and ideally the pasted questionnaire).

Follow this exact sequence:

1. **Confirm the CID came from Dan in this chat** (the permission classifier
   blocks live queries otherwise). If no CID was given, stop and ask.
2. **Fetch live** via `_local_eval/live_fetch.py`'s `patched_fetch(cid)`; save to
   `_local_eval/data_<client>.json`. If access is denied, report it plainly —
   the audit-only MCC loses prospect links routinely; do not improvise.
3. **Sanity-check the engine read before rendering**: account type, enabled
   campaigns + bid strategies + targets, perf summary (30d vs 12m, ROAS if ecom),
   top issues with severities, strengths. Look for misfires: wrong account type,
   exposure-severity findings on confirmed-small leaks, missing questionnaire
   parses (margin, LTV, stated ROAS target, eCom-or-LeadGen).
4. **Render** with a `_local_eval/render_<client>.py` modelled on an existing one
   (embed the questionnaire verbatim). Print the deck URL.
5. **QA the narrative** (`narrative_output.json`): all issue bodies non-empty,
   titles specific (never a category name), commentary ≤ 90 words, every number
   consistent across slides, linter output clean, tone matches the dial.
6. **If a human deck exists** in `sample_audits/` for the same client, do the
   head-to-head ONLY AFTER ours is rendered (keep ours blind), list what each
   caught that the other missed, and close real gaps as new engine checks
   (live-validate, add signature needle, run `_local_eval/regression.py`).
7. Report to Dan: deck URL, the account story in two sentences, top findings,
   anything needing his eyes (tag-level claims, Premier-Partner wording, etc).
