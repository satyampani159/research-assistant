# fixtures/

Offline corpus so the whole pipeline runs with **no API key and no network**
(`tools.fixtures_enabled()` is true whenever `SERPER_API_KEY` is unset, or when
`SERPER_FIXTURES=1`).

## Provenance — read this before treating these as data

These are **hand-authored, schema-accurate samples**, not captured live
responses. They reproduce Serper's real response schema (`organic[]`, `news[]`,
`publicationInfo`, `citedBy`, …) and use plausible-looking figures for a topic
cluster, so offline runs and unit tests exercise the same parsing code paths as
production. **The numbers are invented — never cite them in a real report.**

Replace them with live captures from `scripts/probe.py` when you want a
byte-for-byte fidelity check against the real API.

| Path | What it is |
|---|---|
| `serper/search-*.json` | `/search` responses |
| `serper/news-*.json` | `/news` responses |
| `serper/scholar-*.json` | `/scholar` responses |
| `pages/*.html` | fetched pages, complete with nav/cookie/ads/footer junk so the strip logic is actually tested |
| `pages/index.json` | url → html filename |

Page fixtures deliberately contain the figures the unit tests assert on
(84%, 30%, 41%, 200,000, 38%/21%, $1.2B, 2 August 2026), including
adversarially plausible wrong variants that `numeric_check` must catch.