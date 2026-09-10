# Reproduction package v2 — what to upload, and where each file comes from

**Target:** a new version of [10.5281/zenodo.17779884](https://doi.org/10.5281/zenodo.17779884)
(v1 published 2025-12-01). Zenodo mints a new version DOI; the concept DOI above keeps
resolving to the latest, so the paper's `\url{}` in the Data availability section does not need
to change.

**Status:** draft. Nothing has been uploaded. Two content decisions are open (§4).

---

## 1. The manifest

| File | v1 | v2 | Where it comes from |
|---|---|---|---|
| `instance_generation.zip` | 7.3 kB | **unchanged** | `FacebookVideosLive18-Analysis/instance_generation/` — no commits since v1 |
| `instances.zip` | 21.6 GB | **unchanged** | the same 40 instances per size; the dataset and generator did not change |
| `llls-simulator.zip` | 5.2 MB | **update** | `codeurjc/llls-simulator`, branch `model` |
| `irace_results.zip` | 10.4 MB | **update** | `irace_results/**/irace.Rdata`, 36 files, 10.5 MB |
| `test_elite_configs.zip` | 41.4 kB | **update** | `test_elite_configs/*.log`, 9 logs |
| `simulated.zip` | 8.8 GB | **drop** | superseded by `steps_full.zip`; `simulated.ipynb` removed with it |
| `steps_full.zip` | — | **new** | `steps_full/` — 912 MB |
| `mediasoup-LLLS-experiments.zip` | — | **new** | `codeurjc/mediasoup-LLLS-experiments`, branch `main` — ~5 MB |
| `hop_latency.zip` | — | **new** | `biginstance2:~/mediasoup-LLLS-experiments/campaign_results/` minus the video — ~200 MB |
| `hop_latency_recordings_{480p,720p,1080p}.zip` | — | **new** | the screen recordings — 14 / 18 / 18 GB |
| `analysis.zip` | 28.4 MB | **update** | this repository |
| `README.md` | 8.4 kB | **update** | `README.md`, already rewritten for v2 |

Your recollection was right: `instance_generation.zip` and `instances.zip` are the only two
that carry over untouched. Everything else moved, because the determinism fix
(`Server.hashCode()`) and the Algorithm C comparator fix changed the numbers, and the whole
grid was re-tuned and re-evaluated on 2026-09-03.

## 2. What changed in each updated file

**`llls-simulator.zip`** — take it from branch `model`, not `main`. That branch is 37 commits
ahead of `main` and is the only place where all four things the paper needs coexist:

- the `Server.hashCode()` determinism fix and the `STEPS_FULL` performance work;
- the Algorithm C comparator wiring fix;
- the `max_tree_depth` / `avg_tree_depth` per-step instrumentation;
- the offline MILP model (`src/main/java/es/codeurjc/milp/`) and the competitive-ratio study
  scripts under `model/experiments/`.

**`irace_results.zip`** — the twelve re-tuned `all` races plus the per-algorithm A and B races.
Ship the `irace.Rdata` files only, as v1 did: the `irace.log` transcripts are another 1.09 GB
and nothing in the analysis reads them. Exclude the two recovery copies
(`*.crashed-*`, `*.pre-psrace-*`); the README documents how those situations were handled.

**`test_elite_configs.zip`** — nine logs: `{a_,b_,}{small,medium,big}.log`. The A and B logs
are the instance-30 re-runs; the bare ones are the twelve-elite Strategy C grid.

**`analysis.zip`** — gains `final_experimentation/`: the scripted pipeline that replaced the
hand-run notebooks, including the three latency-conversion scripts
(`viewer_latency_dist.py`, `per_viewer_latency.py`, `per_viewer_plot.py`).

**`README.md`** — now documents the competitive-ratio study, the per-hop latency campaign, and
the depth-to-milliseconds conversion, none of which existed in v1.

## 3. What the new files contain

**`steps_full.zip`** (912 MB) — the reduced per-step data for all 1,440 runs of the final
evaluation: one summary record per run (`summary/*.jsonl`, 35 MB) with the depth histograms and
the weighted means, and one decimated series per run (`series/`, 877 MB). This is what
`depth_analysis.py`, `steps_plots.py` and `viewer_latency_dist.py` read. The full-resolution
archives it was reduced from are several hundred GB and are not shipped.

**The competitive-ratio results ship inside `llls-simulator.zip`**, under `model/experiments/`,
because they are version-controlled in that repository rather than produced outside it. A
separate `competitive_ratio.zip` was drafted and then dropped: it would have been a byte-for-byte
duplicate of a subtree of the simulator archive, and anyone reproducing the study needs the
simulator anyway. What that subtree holds:

- `factorial/` — the 108-cell replicated factorial study: `design.csv` (cells and seeds),
  `observations.csv` (325 rows, one per cell and strategy), `cells.jsonl`, `summary.json`;
- `real-traces/` — LB0 bounds for all 120 instances at the four capacities
  (`real-traces.csv`, 1,440 rows) plus the per-run detail in `by-instance/`;
- the `.md` reports that interpret both.

The 108 tiny instances themselves are not shipped: `SyntheticInstanceGenerator` is deterministic
given its seed, the seeds are in `design.csv`, and the study regenerates them.

**`mediasoup-LLLS-experiments.zip`** (~5 MB) — the testbed source: the master and worker
TypeScript services, the AWS launch and campaign scripts, and `analysis/` with the fitter
(`fit_hop_latency.py`), the robustness checks (`robustness_checks_v2.py`) and the committed
outputs the paper quotes. From branch `main`.

**`hop_latency.zip`** (~200 MB) — the measurements: per (resolution, chain length, repetition),
the per-frame OCR output (`ocr_results.csv`) and the WebRTC statistics of the run (`stats/`),
plus `campaign-manifest.csv` (which records the seeded random order the cells were visited in),
the campaign log and the fit outputs. This is enough to re-derive β and every robustness check
without touching the video.

**`hop_latency_recordings_{480p,720p,1080p}.zip`** (14 / 18 / 18 GB) — the 195 screen recordings
the OCR step reads. Split by resolution so that no single archive is enormous and a reader
interested in one resolution need not fetch the rest. With these the chain is reproducible end
to end: recording → OCR → per-run RTT → fit.

## 4. Decisions taken

**`simulated.zip` is dropped.** Its 8.8 GB were pre-fix, full-resolution CSVs carrying
`nanoTime` seeds and 18 columns with no depth fields; they cannot reproduce any number in the
revised paper and contradict several. Nothing in the paper depends on them any more: Figure 4
now comes from `steps_plots.py --combined` and Table 6 from `paper_eval_tables.py`, both reading
`steps_full/`. `simulated.ipynb`, whose only inputs were those CSVs, has been deleted from this
repository along with the README and `CLAUDE.md` entries that pointed at it.

**The screen recordings are shipped.** `campaign_results/` is 50 GB, of which 48.8 GB is the 195
`.mp4` recordings. Including them makes the OCR step reproducible rather than only the fit, so
the whole chain from raw capture to the 0.091 ms/hop figure is in the package. They are split by
resolution into three archives of 14, 18 and 18 GB rather than one 49 GB file, so that a reader
who only wants one resolution need not download all of it, and so no single upload is enormous.
Note the video is already compressed --- the archives group the files, they do not shrink them.

**The testbed code ships as its own archive.** `mediasoup-LLLS-experiments.zip` holds the source,
separately from the measurements in `hop_latency.zip`, mirroring how `llls-simulator.zip` is
separate from `irace_results.zip` and `test_elite_configs.zip`. The earlier draft folded the code
into `hop_latency.zip`, which was inconsistent with the rest of the package and would have buried
a 5 MB source tree inside a multi-gigabyte download.

## 5. Before uploading

- [ ] **Merge `llls-simulator` branch `model` into `main`**, or tag it. It is 37 commits ahead
      and holds the MILP model and every fix behind the revised results.
- [x] ~~Commit the modified files on `biginstance:~/llls-lb0`~~ — done as `bceb76e`,
      "Run LB0 over the paper's full instance set". The competitive-ratio table for the
      production traces reproduces from the committed `real-traces.csv`, cell for cell.
- [x] ~~Merge `mediasoup-LLLS-experiments` branch `paper-revision/modernise-stack`~~ — merged
      and pushed to `main` (`4794d59..e565215`); the working copies here and on `biginstance2`
      are both on `main`.
- [ ] Merge this repository's `reproduction-package-prep` branch.
- [ ] Rebuild `analysis.zip` from a clean checkout, so no gitignored outputs leak in.
- [ ] Check the README's file listing against what is actually uploaded — v1 shipped
      `instance_generation.zip` while the README called it `instance-generation.zip`.
- [ ] Note in the Zenodo version description that the results were re-tuned and re-evaluated
      after the determinism and comparator fixes, so v1 and v2 numbers differ.

## 6. Not part of the package

- The FacebookVideosLive18 dataset itself — third-party, linked from the README.
- `experiment_results-2026-08-thesis-grid` on `biginstance2` (61 GB): the earlier hop-latency
  campaign that addressed inter-worker hops over public IPs. It is the reproduction package of
  thesis §4.3 ([10.5281/zenodo.17661261](https://doi.org/10.5281/zenodo.17661261)) and is not
  what the paper quotes.
- The `irace.log` racing transcripts (1.09 GB) and the raw `STEPS_FULL` tar archives.
- The full-resolution `STEPS_FULL` CSVs the reduced `steps_full/` was derived from: several
  hundred GB, and the reduction is deterministic.
