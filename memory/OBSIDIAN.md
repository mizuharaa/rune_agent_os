# Obsidian wire

vault: C:\Obsidian_Brain\Daniel_Obsidian_Vault

- Rune **reads** anywhere in the vault, **writes only under `Rune/`** inside it
  (Hermes cards land in `Maestro/Hermes/`, with a browsable MOC at
  `Maestro/Hermes/_index.md` regenerated on every note).
- No naked facts: every memory write carries a `source` and a freshness stamp
  (`fresh_until`). The pipeline refuses writes without a source.
- Anti-rot cadence (part of /skill review week): `python memory/pipeline.py dedup`,
  then `consolidate`, then `archive` — stale + unreferenced notes move to
  `memory/archive.jsonl`, they are never silently deleted.
- Verify the wire any time: `python memory/pipeline.py vault`
