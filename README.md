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

Each subdir is one `corpus_class` column in the results DB. There are two
size tiers, and the split matters more than any individual class.

**Small tier** (`make seed-corpora`, selector `small`). Files capped at
64 KiB, ~250 KiB per class. Sized that way deliberately: this harness was
built for the RAR 1.4 sweep, and RAR 1.4's dictionary is 64 KiB, so a bigger
file adds no compression headroom for it. Classes: `bin-elf`, `bin-shell`,
`doc-text`, `downloads`, `fonts-ttf`, `headers`, `icons-png`, `logs`,
`manpages`, `mixed`.

**Large tier** (`make seed-corpora-large`, selector `large`). ~4 MB per class.
Nothing in the small tier reaches 128 KiB, so a RAR 5 writer's dictionary
sizing, block sizing and filter region screening are all invisible to it, and
the gap it reports against WinRAR (about +3%) is a third of what real files
show (+11% to +30%).

| subdir                 | shape                                                        |
|------------------------|--------------------------------------------------------------|
| `large-bin-stripped`   | multi-MB ELF, no debug data, one big code region             |
| `large-bin-unstripped` | debug sections outnumber code; breaks naive filter screening |
| `large-text`           | prose at a size where the dictionary decides the outcome     |
| `large-source-tree`    | tar of source: many small files, heavy repetition            |
| `large-incompressible` | already-compressed `.zst` / `.xz`                            |
| `large-compressible`   | long runs and a repeating phrase                             |

Both tiers are throwaway and gitignored. Everything is seeded from local files
or generated deterministically, so reseeding produces identical bytes and the
oracle encode cache stays valid.

Other helpers: `scripts/fetch-silesia.sh` and `scripts/fetch-calgary.sh` fetch
canonical benchmark corpora, unused by the seeded tiers;
`scripts/sample-bin.sh [N]` copies N random ELF binaries from `/usr/bin`.

## Running a sweep

```sh
make seed-all            # both tiers
make sweep-large         # rar50 over the large tier — the "how do we compare" run
make triage              # ranked table from the newest results DB
make report              # standalone HTML with bar charts
```

`report` takes several DBs and stacks them as sections, which is how to compare
a change against a baseline, or the two corpus tiers against each other:

```sh
rars-bench report results/large50.db results/rar50.db \
  --label "Large tier" --label "Small tier" --out results/report.html
```

Or directly, which is what the make targets do:

```sh
rars-bench sweep --format rar50 --corpus large --out results/large50.db
rars-bench triage results/large50.db --by ratio-gap --top 20
```

`--corpus` takes `all`, the tier selectors `small` and `large`, or explicit
class names. The rar14 and rar20 targets use `small`, because the large tier
costs time and tells them nothing; everything from rar29 up uses both.

The rars binary is found via `$RARS_BIN`, then a sibling `../rars` checkout,
then `~/src/rars`. Set `$RARS_REPO` for the seeder if the checkout is
somewhere else again.

## The quick tier

```sh
rars-bench quick --rars-bin /path/to/rars
```

Deterministic only, so a loaded machine does not matter. Every format rars
writes, levels 1-5, solid and not, over the corpus classes: packed bytes
against the format's era reference from the oracle cache (encoded once under
wine or DOSBox when missing), `unrar t` on every archive rars wrote, the level
ladder (level n must not pack larger than level n-1) and thread identity (the
`large-*` and `edge-*` classes again at `tmax`, which must give the same
bytes as `--threads 1`). A broken check is listed and the exit status is 1.
It writes `<machine-id>-quick.json` into the history store.

## The release bench

```sh
make release-bench RARS_BIN=/path/to/pinned/rars
rars-bench release-compare results/release-A.json results/release-B.json
```

One fixed scenario set (`src/rars_bench/release/`) that answers speed, peak
memory, size and correctness against RAR per scenario: stored volume sets,
a 1 GiB RAR 5 member at 1, 4 and 16 threads, 20,000 small files, a seeded
DiscMaster sample tested against unrar's verdict, RAR 5 and RAR 4 writing at
matched levels and threads, and the format sweep above for every format.
Timing compares only native binaries (rar 7.12, rar 3.93, unrar, rarfast).
Inputs are generated from `/usr` and seeded random data into `.release/cache`
and reused. `--scale tiny` runs it all in a few minutes to check the runner.
Output is `results/release-<date>-<commit>-<scale>.{json,md}`, with every
format-sweep cell in `format-cells.tsv` beside them, worst gap first.

## History store

`results/<rars version or tag>/<machine-id>-<tier>.json`, committed. Each file
holds the machine's profile (CPU, L3 domains, the CPU set each thread count ran
on, kernel, filesystem, load at start and end), every tool's version and
sha256, and one entry per cell with its raw metrics and its ratios to the
reference tool from the same run. Cells are keyed by thread label (`t1`, `t4`,
`tmax`), not thread count, so they line up across machines; ratios compare
across machines, raw times and memory only on the same one. The machine id is
a CPU-model slug plus a hash of `/etc/machine-id`.

References are entries in `src/rars_bench/registry.py`: a name, how to find the
binary, how to read its version, and the cells it is the reference for. A
cell's ratios line up across results only against the same tool name, and a
tool whose version or hash changed between two results is flagged.

`rars-bench release` writes one (`-timed.json`) beside its usual output.
`rars-bench history-import` files an older release-bench JSON into the store.

Anything else in `results/` (`release-*.json`, `*.db`, run directories)
predates this layout, stays local and is ignored by git.

## Result schema

See `src/rars_bench/schema.py`. One row per (encoder × variant × corpus_path)
compress operation. Triage queries join `rars` and `rar` rows on the same
`(format, method, filter, solid, encrypted, dict_log, corpus_path)` key.

## Caveats baked in from day one

- **Oracle timings are not measurements.** Wine startup measures ~110 ms,
  but a live `rar t` on the same archive varied 267 ms warm to 998 ms cold,
  and `encode_ms` for an oracle row is replayed from the encode cache rather
  than timed. Read oracle rows for bytes, never for speed. Note also that
  `decode_ms` is the *cross*-decoder's time: on a `rars` row it is how long
  WinRAR took to test our archive, which reads backwards from what the column
  name suggests.
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

Working. Harness, drivers, oracle cache and triage CLI are all implemented and
run. What is known to be wrong, in the order it is worth fixing:

1. **Timings are not trustworthy.** See the caveat above. The fix is probably
   to stop recording oracle timings entirely and report speed only for rars,
   measured natively, rather than to keep patching an emulated number.
2. **No internal-decision capture.** When ratios diverge we still cannot see
   whether rars picked a different filter, missed a long match or chose a
   different block size. `rars a --report-json` emitting
   `{method_chosen, filters_emitted, block_count, dict_size_used}` is still
   the right hook, and still unbuilt.
3. **Variant axes beyond method.** Filter on/off, solid, encrypted and dict
   size are declared in the format modules but the sweeps drive method only.

The corpus caveat that motivated the large tier is worth stating plainly for
whoever reads a result table next: this harness began as proof-of-concept
checking that a from-spec RAR writer produced decodable archives at all, and
its corpus was sized for that. It answers "is our compression competitive"
only since the large tier existed.

## Conventions

- Python 3.11+, pytest functional style, no mocks.
- Make-installed venv at `.venv/`.
- WTFPL.
- Project under test: `../rars/`.
