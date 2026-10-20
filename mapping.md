# Mapping: Algoritmekader CSV → ontology

## Source
Data comes from the MinBZK Algoritmekader repository
(https://github.com/MinBZK/Algoritmekader), commit `6ee2c259fae80e439d738472e815565044a840b5`
(6 August 2026). The markdown files were converted to CSV by
`audit-ontology/src/mine_vereisten_v3.py`. The import script is
`audit-ontology/src/csv_to_ttl.py`.

## Scope of v0.1
Only individuals (instances) are created: one per row. Links between
individuals and the other columns are not imported yet.

## Mapping per file

| CSV file | Rows | Class | IRI pattern | ID column | Label |
|---|---|---|---|---|---|
| vereisten.csv | 62 | `Requirement` | `requirement-<id>` | `requirement_id` | `title` (`@nl`) |
| maatregelen.csv | 108 | `Measure` | `measure-<id>` | `maatregel_id` | `title` (`@nl`) |
| hulpmiddelen.csv | 28 | `Tools` | `tools-<id>` | `hulpmiddel_id` | `title` (`@nl`) |

## Decisions and reasons

- **IRIs include a class prefix.** The ID `dat-01` exists both as a
  requirement and as a measure. Without a prefix both would get the same
  IRI and be merged into one individual that is both a Requirement and a
  Measure.
- **Labels are Dutch.** The source text is Dutch, so labels carry the
  language tag `nl`.
- **Class `Tools`** corresponds to *hulpmiddelen* in the Algoritmekader.

## Not imported yet
All other columns (e.g. `risicogroep`, `rol-ai-act`, `levenscyclus`,
`onderwerp`, `rollen`, `toelichting`, `vereiste`, `maatregel`,
`sources`), the cross-references, and the glossary (`begrippenlijst.csv`).
They will be added one class at a time.

## Known issues in the source data
- 20 of 820 cross-references point to files that do not exist; these will
  be reported, not corrected, when links are imported.