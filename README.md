# rars-bench

Compression-quality and performance harness for [`rars`](../rars) vs reference
RAR / WinRAR.

## Why this is separate from rars

`rars` is a from-scratch Rust RAR implementation. Its in-tree tests verify
correctness on a curated fixture corpus — they do **not** answer:

- Does `rars` produce competitive compression ratios vs real WinRAR?
- Where (corpus type, codec settings) does it lag, and by how much?
- Is encode/decode speed acceptable?
- When ratios diverge, is it a filter selection difference, a match-finder
  weakness, or a wire-format bug?

This harness drives both `rars` and a reference `rar` / `unrar` binary across
shaped corpora, captures ratio + timing + round-trip status into sqlite, and
provides a triage CLI to rank cells by anomaly. The corpus is large and ad hoc
(we sample from `/usr/bin`, fetch standard benchmark corpora, etc.), runs are
long, results are noisy — none of which belongs in `rars`'s own CI.

## Repo layout

```
rars-bench/
  src/rars_bench/
    harness.py       # core runner: invoke rars + rar, parse, persist
    schema.py        # sqlite result schema + init_db()
    triage.py        # ranked-query CLI
    formats/
      rar5.py        # RAR5 variant matrix
      # rar29.py, rar15_40.py, rar13.py — added one at a time
  corpora/           # gitignored; populated by scripts/
    text/
    binary-x86_64/
    incompressible/
    ...
  results/           # gitignored; sqlite DBs per run
  scripts/
    fetch-silesia.sh
    fetch-calgary.sh
    sample-bin.sh
  tests/             # pytest, on tiny synthetic corpora
```

## Approach

**One format at a time.** For each format:

1. Pick a starting variant (RAR5: `m1` and `m3`, no filter, no solid, no
   encryption).
2. Run it across every corpus subdir.
3. Triage by ratio gap and decode-failure rate.
4. Add one variant axis (filter on/off, then solid, then encrypted, then dict
   size, then methods 0/2/4/5).
5. When the format's clean, move to the next.

This keeps the matrix tractable and gives a stable rhythm we can copy across
formats.

## Corpus shape

Each subdir is one `corpus_class` column in the results DB. Add subdirs ad
hoc as new test types come up.

| subdir              | shape                                   |
|---------------------|-----------------------------------------|
| text                | source trees, prose, JSON, CSV          |
| binary-x86_64       | ELF samples, exercises x86 filter       |
| binary-arm          | exercises ARM filter                    |
| incompressible      | random bytes, already-compressed (.zst) |
| highly-compressible | zeros, repeated patterns                |
| many-small          | thousands of tiny files (solid mode)    |
| few-large           | video chunks, db dumps                  |
| audio-wav           | exercises delta / audio filter          |
| edge-sizes          | 0B, 1B, 64KiB±1, dict-boundary          |
| mixed               | tar of a real project                   |

Helpers:

- `scripts/fetch-silesia.sh`, `scripts/fetch-calgary.sh` — populate canonical
  benchmark corpora.
- `scripts/sample-bin.sh [N]` — copy N random ELF binaries from `/usr/bin`.

## Running a sweep

(harness not yet implemented — see Status below)

```sh
make install
./scripts/sample-bin.sh 50
rars-bench sweep --format rar5 --variants m1,m3 --out results/2026-05-07.db
rars-bench triage results/2026-05-07.db --by ratio-gap --top 20
```

## Result schema

See `src/rars_bench/schema.py`. One row per (encoder × variant × corpus_path)
compress operation. Triage queries join `rars` and `rar` rows on the same
`(format, method, filter, solid, encrypted, dict_log, corpus_path)` key.

## Caveats baked in from day one

- **Wine `rar` startup is ~1s.** For sweeps over many small files, batch
  inputs into a single `rar a @filelist` invocation. Per-file timing is
  approximate; per-archive ratio is exact.
- **Encrypted runs lose byte-equality.** `rars` uses `getrandom` for salts
  and IVs, so encrypted output bytes differ run-to-run. `roundtrip_status`
  for encrypted runs only tracks decode success, not byte-equality.
- **Internal-decision capture.** When ratios diverge we want to know why —
  did `rars` pick a different filter, miss a long match, choose a different
  block size? Plan: add `rars a --report-json <path>` to emit
  `{method_chosen, filters_emitted, block_count, dict_size_used}` to a
  sidecar JSON. Until that flag ships in `rars`, divergence triage is
  wire-format archaeology.

## Status

**v0 skeleton.** Plan and structure committed; harness and triage CLI not yet
implemented. Pick up in this order:

1. `harness.py` — invoke `rars a` and `rar a` on a single file, capture ratio
   + timing into sqlite. Smoke-test on `corpora/edge-sizes/`.
2. `formats/rar5.py` — declare the RAR5 m1+m3 starting variant. Drive
   `harness.py` across the corpus subdirs.
3. `triage.py` — `triage by-ratio-gap`, `triage decode-failures`, `triage
   drill --corpus-path <path>`.
4. `scripts/fetch-silesia.sh` / `fetch-calgary.sh` — populate standard
   corpora.
5. Add filter on/off variant axis; rerun.
6. Add solid; rerun.
7. Add encryption; rerun.
8. Add dict size; rerun.
9. Add methods 0/2/4/5; rerun.
10. Move to next format (RAR2.9 first — most complex codec, biggest
    surface).

Open question to revisit: propose `rars a --report-json` as a follow-up task
for codex on the `rars` side, so the introspection hook is ready when the
harness needs it.

## Conventions

- Python 3.11+, pytest functional style, no mocks.
- Make-installed venv at `.venv/`.
- WTFPL.
- Project under test: `../rars/`.
