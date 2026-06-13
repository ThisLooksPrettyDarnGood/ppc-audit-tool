# PPC Audit Tool — project instructions

Generates client-ready Google Ads audit decks (Google Slides) from live API data.
Owner: Dan Trotter (PPC Team / PPC Geeks). Dan does not code — explain plainly,
proceed autonomously on reversible work, and never leave a deck half-rendered.

## Pipeline (one straight line)

```
fetch_account_data.py   → pulls ~40 live Google Ads queries into one data dict
analyse_account.py      → deterministic engine: ~30 expert checks → ranked findings
generate_narrative.py   → GPT narration per slide + style LINTER (deterministic)
populate_slides.py      → copies the Slides template, fills tokens, swaps dial/logo
app.py                  → Streamlit front end (Dan presses the button)
```

`_local_eval/` (gitignored — client data + secrets live here) has the harnesses:
- `live_fetch.py CID "GAQL"` — validate any new query live BEFORE wiring it in
- `render_<client>.py` — full pipeline from a saved `data_<client>.json` to a deck
- `repopulate.py` — re-fill slides from the EXISTING narrative_output.json
  (surgical fixes: never re-roll GPT narration to change one word)
- `regression.py` — engine snapshot across all saved accounts; run before every
  commit that touches analyse_account.py (`--update` to re-baseline deliberately)

## Hard rules (most are enforced in code — keep it that way)

- **Never confidently wrong.** If the API can't prove it, the deck hedges or says
  what would confirm it. Web/purchase Enhanced Conversions, Consent Mode and
  tag-level errors are NOT API-visible — never claim anything about them.
- **Severity follows MEASURED money**, never "exposure". A confirmed-small leak is
  an Observation, not a headline (geo £68/5% lesson, Display 11p lesson).
- Copy rules live in `_lint_narrative()` in generate_narrative.py — banned words
  (em-dashes, "misdirected", "undervalued", hard conversion minimums…), ecommerce
  vocabulary (shoppers/orders, never "lead demand"). New copy rule from Dan =
  new linter entry FIRST, prompt tweak second. Prompts are suggestions; the
  linter is law.
- Whole pounds (£239, never £239.58). Percentages alongside "X of Y" counts.
  Name up to 3 countries/places when a finding has them. Entity labels always
  ("the 'Dynabrade Tools' CAMPAIGN"). "Performance Max (PMax)" then "PMax".
- Tone follows the dial: amber_red/red decks never open with praise, and ROAS
  below the client's break-even line is never called "strong".
- Statistical honesty: 1–2 conversions on a term is an early signal, not proof.
- 12-year-old reading level; one idea per sentence; British English.

## Workflow gotchas

- **Ask Dan to confirm a CID in-chat before live queries** — the permission
  classifier blocks CIDs not mentioned this session. MCC 539-263-1535 is the
  AUDIT-ONLY MCC; prospects unlinking after an audit is normal (guardrail exists).
- Never commit client data: data files, questionnaires, deck PDFs/PPTXs all live
  in `_local_eval/` or `sample_audits/` (both gitignored).
- When adding a fetch: wrap the call site in try/except with a printed skip line —
  the analyser must degrade gracefully when a query fails (cautious default).
- New checks need: the check, a `_ISSUE_SIGNATURES` needle (first match wins —
  insert specific needles ABOVE general ones), live validation on a real CID, and
  a `regression.py` run across all saved datasets.
- Per-action data used in one finding must come from the SAME fetch pass as data
  used in another (the 41-vs-42 lesson: piecemeal merges drift).
- Deploy: a signature-changing push can throw a stale-import TypeError in
  Streamlit — reboot the app. Always send Dan the NEWEST deck URL.

## Session context

Dan numbers chats "step 8, 8.1 … 9, 10" (steps 1–7 were in Cowork, consolidated
into the step-8 handoff). Detailed running history lives in the auto-memory
(`~/.claude/projects/...ppc-audit-tool/memory/` — read MEMORY.md index first).
Pending Dan decisions: em-dashes in the static template sales slides (Max's
original copy). (Premier-Partner wording confirmed fine, 13 Jun 2026.)
