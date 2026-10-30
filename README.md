# Ananda dealflow showcase

This is a small, offline demonstration pipeline for an AI & Automation Manager at Ananda Impact Ventures in Munich. It reads synthetic pitch emails, normalises them into deal records, removes duplicates (same company name and company domain; on free-mail domains such as gmail.com the full address is used), validates extraction, scores against a transparent demo thesis, stores accepted deals in SQLite, and writes a Markdown digest of the deals and review items seen in the current ISO week.

```sh
python3 -m dealflow run samples/ --db out.db --digest out.md
python3 -m unittest discover -s tests   # 23 tests
```

The extractor is a pluggable `Extractor` interface returning fields plus confidence. The default `RulesExtractor` is deterministic and intentionally not an LLM. Invalid, incomplete, or low-confidence records go to the `reviews` table rather than being guessed. Contact emails are retained in the SQLite `deals` table (a real deployment would need a GDPR basis and retention policy for them) for the investment team; emails and phone numbers are redacted in the digest, review storage, and any human-facing log messages. The sample companies and domains are fake.

## Review

The review findings are addressed with confidence-aware validation and human review routing, one shared name/domain dedupe key, idempotent SQLite constraints, per-criterion score explanations, review items in the digest, and round-trip tests for loading, persistence, redaction, and digest output.

A second review round (Grok Build) found more: unreadable `.txt` files and malformed records aborted the run (now they go to the review queue), distinct gmail.com contacts with the same company name were merged, the Europe list was too short, multi-word impact areas never matched and the reasons ignored the thesis, some phone formats (`+ 49 …`, `(0)30 …`) escaped redaction, and the digest reprinted all history. All fixed, with four new tests.

Honest limits: this is a demo, not an investment decision system. Parsing only understands labelled plain text, the Europe list is a fixed country set, scoring is simplistic, SQLite has no authentication or migrations, and no email/API/network integration is included. Human review and proper data governance remain essential.
