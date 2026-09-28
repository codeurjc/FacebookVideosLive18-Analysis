# WebRTC media server interconnection strategies for scalable low-latency live streaming sessions

Reproduction package for the paper "WebRTC media server interconnection strategies for scalable low-latency live streaming sessions". This description contains detailed steps to reproduce the results on the paper.

The complete reproduction package can be found in Zenodo ([https://doi.org/10.5281/zenodo.22922353](https://doi.org/10.5281/zenodo.22922353)) and contains the following files:

```
.
├── instance_generation.zip         # Scripts used to generate random instances based on FacebookVideosLive18 dataset
├── instances.zip                   # Instances generated for the experiments in the paper, separated by instance size.
├── llls-simulator.zip              # Simulator source code, including the offline MILP model
├── irace_results.zip               # irace results from the experiments in the paper (parameter evaluation results)
├── test_elite_configs.zip          # Final evaluation results
├── steps_full.zip                  # Reduced per-step data: one summary record and one decimated
│                                   # series per run of the final evaluation grid
├── mediasoup-LLLS-experiments.zip  # Testbed source code for the per-hop latency campaign
├── hop_latency.zip                 # Per-hop latency measurements: per-run OCR output, statistics and fit
├── analysis.zip                    # Analysis scripts and Jupyter Notebooks
└── README.md                       # This file
```

Note: in instances.zip, instances with numbers 0 to 29 correspond to the training set, and instances with numbers 30 to 39 correspond to the test set.
This repository contains scripts and notebooks for analyzing live video data from the FacebookVideosLive18 dataset and generating pseudo randomized instances for simulation. The 120 generated instances (40 each of small, medium and big) are collectively the paper's **FacebookLLLS** dataset — a new, publicly available dataset of publisher and viewer join/leave events derived from real Facebook Live sessions. "FacebookVideosLive18" refers to the original raw dataset this repository consumes as input; "FacebookLLLS" is the derived dataset this repository produces and that the paper evaluates against.

## Index

- [Requirements](#requirements)
- [Instance generation](#instance-generation)
- [Simulation](#simulation)
  - [Parameter evaluation](#parameter-evaluation)
    - [Two machines](#two-machines)
    - [Holding races back, and draining a machine](#holding-races-back-and-draining-a-machine)
    - [When a race dies mid-flight](#when-a-race-dies-mid-flight)
    - [Picking the elite configurations for the final evaluation](#picking-the-elite-configurations-for-the-final-evaluation)
  - [Final evaluation](#final-evaluation)
    - [When the final evaluation stalls](#when-the-final-evaluation-stalls)
    - [The single-instance sample behind Table 6 and Figure 5](#the-single-instance-sample-behind-table-6-and-figure-5)
  - [Reducing the per-step archives](#reducing-the-per-step-archives)
- [Competitive ratio and the offline optimum](#competitive-ratio-and-the-offline-optimum)
- [Per-hop latency on real media servers](#per-hop-latency-on-real-media-servers)
- [Analysis](#analysis)
  - [Final experimentation (scripted)](#final-experimentation-scripted)
  - [Interconnection depth in milliseconds](#interconnection-depth-in-milliseconds)
  - [Notebooks](#notebooks)

## Requirements

The following software versions were used:

For generating instances:

- Ubuntu 22.04
- Python 3.11

For running the simulations:

- Ubuntu 22.04 or newer
- Docker (for parameter tuning)
- Java 21 or later and Maven 3.9 (for the final evaluation)
- `zstd`, for the per-step archives the final evaluation writes
- GNU tar 1.32 or newer

Both steps are long and want a large machine: the tuning grid is about 6,800 core-hours and
the final evaluation writes several hundred GB of per-step archives. Sizes and timings quoted
below were measured on a 64-core, 118 GB machine with an 800 GB data volume.

For the offline optimum (competitive ratio study):

- Java 21 and Maven 3.9, as for the final evaluation
- Gurobi 11 with a valid licence, reachable from Maven. Gurobi is commercial; an academic
  licence is free. Only this step needs it --- every other step runs without a solver.

For the per-hop latency campaign:

- An AWS account, and the testbed code in `mediasoup-LLLS-experiments.zip`. This step
  launches and terminates EC2 instances and therefore costs real money; the campaign in the
  paper is 195 runs over about 6.3 hours. The derived measurements are shipped with the
  package, so this step only needs re-running to re-measure, not to re-analyse.

For data analysis:

- Windows 10 (Ubuntu 22.04 can also be used)
- Python 3.11
- R 4.5.0 with irace 4.2.0 package installed

Note that one analysis step, the per-viewer latency distribution, reads the instance event
streams and therefore needs `instances.zip` unpacked (about 100 GB uncompressed). Every other
analysis step works from `steps_full.zip` and `test_elite_configs.zip` alone.

## Instance generation

You can generate instances using the scripts in `instance_generation.zip`, or use the already generated instances in `instances.zip`. `instance_generation.zip` contains a folder with the necessary scripts for generating new instances (the FacebookLLLS dataset). The scripts rely on the raw FacebookVideosLive18 dataset. The dataset can be downloaded from [here](https://sites.google.com/view/facebookvideoslive18/download?authuser=0). Ensure the datasets are placed in the `data/` directory (download both datasets' full compressed files and unzip them in `data/`). The directory structure should look like this:

```
data/
├── January_February_dataset/
└── June and July dataset/
instance_generation/
├── generate_instances.py
├── generate_instances_tasks.py
└── instance_validator.py
```

Then, install the packages needed and run the script in the `instance_generation/generate_instances.py` directory to generate instances.

```bash
pip install -r requirements.txt
python3 instance_generation/generate_instances.py
```

The instances will be generated in the `instances/` folder, separated by instance size (`instances/instances-<size>/instance-<size>-<n>.csv`). A `sessions/` folder will also be created containing the session data used for generating the instances.

The simulation steps below read instances from a `$DATA_DIR` split into a training and a test
set, which is what keeps either step from having to move files around. To go from the
generator's output to that layout:

```bash
for size in small medium big; do
    mkdir -p "$DATA_DIR/train/$size" "$DATA_DIR/test/$size"
    for n in $(seq 0 29);  do cp "instances/instances-$size/instance-$size-$n.csv" "$DATA_DIR/train/$size/"; done
    for n in $(seq 30 39); do cp "instances/instances-$size/instance-$size-$n.csv" "$DATA_DIR/test/$size/";  done
done
```

## Simulation

The simulation source code is in `llls-simulator.zip`. Unzip the file.

### Parameter evaluation

The paper tunes **36 separate irace races**: an all-strategies race for every (instance size,
capacity) pair, plus a per-strategy race for A and for B over the same pairs.

| Races                                        | `maxExperiments`                          | Count |
| -------------------------------------------- | ----------------------------------------- | ----- |
| all strategies (A, B and C sampled together) | small 75,000 · medium 35,000 · big 20,000 | 12    |
| Strategy A only                              | 1,000                                     | 12    |
| Strategy B only                              | 5,000                                     | 12    |

Each race needs its own scenario (the budget depends on the instance size), its own parameter
file (`maxReservation` ranges over `[1, C/2]` — so 25, 75, 325, 500 — and the per-strategy
races expose only that strategy's parameters) and its own target-runner (the capacity is baked
into it, not passed as an argument). Only the training instances of one size may be visible to
a race, and only the test instances of that same size.

`run-tuning.sh` does all of that. It generates the three files per race from the templates in
`tuning/templates/` and _mounts_ the right instance directory, so no file on disk is edited or
moved between races:

```bash
./run-tuning.sh              # the whole 36-race grid
./run-tuning.sh --list       # print the planned grid with cost estimates, then exit
./run-tuning.sh --status     # progress of a grid already in flight
./tuning-status.sh           # the same, consolidated across every machine on the grid
./tuning-status.sh --watch   # ...refreshed every 60s
./tuning-hold.sh --show      # which races are held out of the grid
./recover-race.sh --check all/big/50   # is that race's irace.Rdata a result or a wreck?
```

`tuning-status.sh` is a separate, read-only script precisely so that it can be added or
edited while a grid is running — which `run-tuning.sh` cannot be. It reports each race as
done / RUNNING / CRASHED / held / claimed / pending, says which machine is running what and how much of its
budget is left, and projects the finish from the work remaining and the number of slots
across all machines (`TOTAL_SLOTS`) rather than from the observed completion rate, which is
meaningless until several races have finished. A race that has been running long enough to
have timings of its own is weighed by those rather than by the estimate, and the projection is
reported as a floor, because every race timed so far has run slower than estimated.

Instances are read from `$DATA_DIR`, which must be laid out per size — the training set
(instances 0–29) and the test set (30–39) kept apart:

```
$DATA_DIR/
├── train/{small,medium,big}/instance-<size>-{0..29}.csv
└── test/{small,medium,big}/instance-<size>-{30..39}.csv
```

Everything else comes from the environment; the script itself is never edited:

| Variable                             | Default                         | Meaning                                                           |
| ------------------------------------ | ------------------------------- | ----------------------------------------------------------------- |
| `DATA_DIR`                           | `/mnt/instances/data`           | instances, laid out as above                                      |
| `RESULTS_DIR`                        | `/mnt/instances/tuning-results` | where finished races are filed                                    |
| `WORK_DIR`                           | `/mnt/instances/tuning-work`    | generated race directories (removed on success)                   |
| `PARALLEL`                           | `nproc`                         | irace's `parallel`, i.e. concurrent target-runner calls           |
| `PARALLEL_BIG`                       | `$PARALLEL`                     | same, for big instances only — they are the memory-hungry ones    |
| `KINDS`                              | `all A B`                       | which race kinds to run                                           |
| `SIZES`                              | `small medium big`              | which instance sizes                                              |
| `CAPACITIES`                         | `50 150 650 1000`               | which capacities                                                  |
| `RACES`                              | —                               | explicit race ids (`all/big/50 B/small/150`), overriding the grid |
| `BUILD_IMAGE`                        | `1`                             | set to `0` to skip `docker build`                                 |
| `COORD`, `QUEUE_DIR`, `RESULTS_SYNC` | —                               | see _Two machines_, below                                         |

The budgets are the paper's and are deliberately **not** configurable.

Sizing the parallelism: one target-runner call is a JVM that peaks at about 1.5 GB on big
instances, so the usable figure is whichever is smaller of the core count and RAM ÷ 2 GB. On a
64-core, 118 GB machine `PARALLEL=62` is comfortable; on a 32-core, 59 GB machine use about 30.

Each finished race is filed in the layout the analysis expects, together with the exact
scenario, parameter file and target-runner it ran under, so the race stays reconstructible:

```
$RESULTS_DIR/
├── 75000_maxExp/small/{50,150,650,1000}/     # all-strategies races
├── 35000_maxExp/medium/…
├── 20000_maxExp/big/…
└── per_algorithm/{A,B}/<size>/<C>/
        ├── irace.Rdata      # what the analysis reads
        ├── irace.log        # irace's own output
        ├── scenario.txt, parameters.txt, target-runner
        └── race.json        # budget, parallelism, wall time, host
```

A race that already holds an `irace.Rdata` is skipped, so an interrupted grid resumes by
re-running the same command.

**Do not disable elite testing.** `testIterationElites` and `testNbElites = 5` in the scenario
template look like a tuning-time luxury, but `irace.ipynb` reads
`iraceResults$testing$experiments` and cannot compute mean RPD or rank without it.

#### Two machines

Races are claimed one at a time through an atomic `mkdir` in `$QUEUE_DIR` on a coordinator, so
two machines can share one grid and whichever is free takes the next race. This matters because
the races are wildly uneven — the largest is estimated at ~1,900 core-hours and the smallest at
0.6 — so a fixed split wastes a lot. Races are handed out longest-first for the same reason.

Start the coordinator normally, then on the second machine point `COORD` at it:

```bash
# coordinator (owns the queue)
PARALLEL=62 ./run-tuning.sh

# joining machine
COORD=ubuntu@10.0.0.1 PARALLEL=30 \
  DATA_DIR=$HOME/data WORK_DIR=$HOME/tuning-work RESULTS_DIR=$HOME/tuning-results \
  ./run-tuning.sh
```

The joining machine rsyncs each finished race back to the coordinator (`RESULTS_SYNC`, which
defaults to the coordinator's `RESULTS_DIR`), so results collect in one place and `--status` on
the coordinator sees the whole grid.

#### Holding races back, and draining a machine

Both of these come up mid-grid, and neither may disturb a race in flight — a big race can
represent days of compute.

_Keeping races out of the grid._ A claim that already exists reads to a worker as "another
machine has this race", so creating one by hand reserves a race for nobody and workers move
on to the next. `tuning-hold.sh` does that in bulk, marking each hold with a `HOLD` file so
releasing one can never remove the claim of a race that actually ran:

```bash
./tuning-hold.sh --hold A B      # run only the all-strategies races
./tuning-hold.sh --release A B   # give them back
```

Only the twelve all-strategies races feed the paper; the A and B races exist for the
per-strategy comparison. Holding A and B cuts the grid by two thirds of its races.

_Taking a machine out of the grid._ A worker claims its next race by running `mkdir` over ssh
on the coordinator, so making that ssh fail makes every claim fail — and a failed claim is
exactly what the worker reads as "someone else has it". It finishes the race it is inside of,
writes the result to its own disk, skips the rest of its list and exits. Add to the worker's
`~/.ssh/config`:

```
Host <coordinator-ip>
    ProxyCommand /bin/false
```

Deleting those two lines and re-launching `run-tuning.sh` puts the machine back to work. The
same gate blocks the worker's `RESULTS_SYNC` push, so its finished races stay local, with a
`[sync] FAILED, result kept locally` line in its log. Pull them in from the coordinator, which
needs nothing from the worker but a `race.json`:

```bash
WORKER=ubuntu@10.0.0.2 ./collect-worker-results.sh            # once
WORKER=ubuntu@10.0.0.2 ./collect-worker-results.sh --watch    # until the worker goes idle
```

#### When a race dies mid-flight

A race can crash after days of work, and the failure is quiet. irace writes its log file at the
end of every iteration, so a run that dies leaves an `irace.Rdata` behind — but a _recovery
checkpoint_, holding only `scenario`, `irace_version` and `state`, where a finished race holds
`allElites`, `experiments`, `allConfigurations` and `testing`. `run-tuning.sh` files it and
reports `[done]` whatever the exit code, and every status view then counts the race complete.
Nothing goes wrong until the analysis tries to read the elite configurations out of it.

`race.json` records the real exit code, so `tuning-status.sh` shows such a race as `CRASHED`
rather than `done`, and prices its unfinished budget back into the work remaining:

```
all/big/50       CRASHED   exit=139, 10913 of 20000 budget left; ./recover-race.sh all/big/50
```

The checkpoint is not a loss. `--recovery-file` replays the finished iterations in seconds and
restarts irace at the iteration that was interrupted; only the experiments in flight inside
that iteration are repeated:

```bash
./recover-race.sh --check all/big/50    # report only: exit code, shape of the .Rdata, budget left
./recover-race.sh all/big/50            # archive the wreck alongside, resume from it
PARALLEL=32 ./recover-race.sh all/big/50
```

The crashed race is archived as `<race-dir>.crashed-<timestamp>` rather than deleted: it holds
the only copy of the checkpoint and of the first log, and the recovered race is reconstructible
only with them. On success the two logs are concatenated in the order they ran, so the race
keeps one continuous `irace.log`. The script refuses to start while another race is running on
the same machine — two races means twice the JVMs on the same cores and the same RAM — unless
`CONFIRM_BUSY=1` says the double-booking is deliberate.

_Queueing a recovery behind the race in flight._ `recover-race.sh` will not start while
another race is up, and the machine never goes idle by itself — `run-tuning.sh` claims the
next race the moment the current one ends. Holding the races that have not started yet makes
that next claim fail, which the runner reads as "another machine has it": it walks the rest of
its list, starts nothing, and exits without disturbing the race it is inside of.

```bash
./tuning-hold.sh --hold all      # the runner will stop after its current race
setsid nohup ./recover-when-idle.sh all/big/50 \
    >> /mnt/instances/recovery-queue.log 2>&1 < /dev/null &
```

`recover-when-idle.sh` waits for the runner _and_ every race container to be gone, recovers
the race with the whole machine, then releases the holds and relaunches the grid — which skips
every race that already has an `irace.Rdata`, the recovered one included. `RESUME=0` stops
after the recovery instead. Serialising this way costs nothing overall: the core-hours are the
same either way, and the recovered race finishes in half the wall time it would take sharing
the machine. While the holds are in place `tuning-status.sh` counts only the unheld races, so
its totals read against a much smaller grid until the holds come off.

_Giving a drained machine one last race._ A drained worker cannot claim anything: its gate to
the coordinator always fails. `queue-race.sh` waits for it to finish what it is doing and then
re-launches `run-tuning.sh` with `RACES` set to a single race and no `COORD`, so it uses a
queue on its own disk. A one-race list is walked in one pass, so the runner exits when that
race ends and takes nothing else.

```bash
COORD= QUEUE_DIR=$HOME/tuning-queue DATA_DIR=$HOME/data RESULTS_DIR=$HOME/tuning-results \
    setsid nohup ./queue-race.sh all/medium/50 >> ~/queue-medium50.log 2>&1 < /dev/null &
```

Because that run bypasses the shared queue, **the coordinator must already hold a claim for
the race**, or a second machine will start it too. Create the claim directory without a `HOLD`
marker inside it: a marked claim is a hold, which `--release` gives back, while an unmarked one
reads as "another machine has this" to every worker and survives a release. Drop an `OWNER`
note in it saying which machine took it — nothing parses that file, but the next person to
read the queue will want it. The result stays on the worker; `collect-worker-results.sh` pulls
it in, and once it lands the race is skipped on its `irace.Rdata` like any other.

`queue-race.sh` checks the instance directories, the image and the queue _before_ it starts
waiting, so a machine missing `data/train/medium` says so now rather than in two days.

_Putting a machine back on the shared queue._ The mirror image of draining. A machine that
was given a fixed race list (`RACES=...`) walks it once and stops, and a drained machine
stops at the end of its current race; either way something has to start `run-tuning.sh`
again, this time **with `COORD` set**, so it claims from the coordinator instead of from a
list decided in advance. `join-when-idle.sh` waits for the machine to be free and does that:

```bash
COORD=user@coordinator-host DATA_DIR=$HOME/data \
    WORK_DIR=$HOME/tuning-work RESULTS_DIR=$HOME/tuning-results \
    setsid nohup ./join-when-idle.sh >> ~/join-grid.log 2>&1 < /dev/null &
```

It claims nothing itself — `run-tuning.sh` still claims one race at a time through the
coordinator — so two machines running it never collide and neither can take a held race.
Remember to remove the drain gate from `~/.ssh/config` first: the script checks the
coordinator once when it starts (a warning) and again when the machine goes idle (which
decides), precisely because the gate tends to be forgotten in between.

A worker walks its race list once, in a single pass. Releasing a hold therefore does **not**
send a worker back to a race it has already walked past; re-run `run-tuning.sh` to pick
released races up. Finished races are skipped on their `irace.Rdata`, so re-running is safe.

**Never edit `run-tuning.sh` while a grid is running.** `bash` reads a script by byte offset as
it executes, so rewriting the file in place makes a long-running invocation resume in the middle
of whatever now sits at that offset. Copy the edited version in once the grid has finished.

**Cost.** `--list` prices the grid at roughly 6,800 core-hours, ~78% of it in the twelve
big-instance races. **That is a floor and a loose one.** The per-run times it multiplies were
measured on Strategy C's _elite_ configuration, and irace spends most of its budget on
configurations that are nothing like the elite one: measured against the estimate. Budget four
to five times the printed figure for the big races. Two further reasons the wall time overruns:
irace cannot always fill every parallel slot — in the later iterations of a race the surviving
configurations can number well below `parallel` — and a race with few possible configurations
(Strategy A has four) finishes well short of the nominal speed-up.

`tuning-status.sh` reports the observed per-run time of every race that has one, which is the
number to plan by once a race is under way.

The results of this step for the paper are collected in the `irace_results.zip` file.

#### Picking the elite configurations for the final evaluation

The final evaluation runs the configurations irace chose: for each race the **first**
configuration of its elite set, since irace lists them best-first. Two scripts do this,
reading the `irace.Rdata` files directly, so the elite sets never have to be transcribed:

```bash
python3 final_experimentation/elite_configs.py --results irace_results --out tables
python3 final_experimentation/elites_to_param_file.py \
    --elites tables/best_elites.json \
    --out ../llls-simulator/run-alg-parameters.txt.algc
```

`elite_configs.py` summarises every race and writes `best_elites.json`, the winner of each
of the twelve `all` races. `elites_to_param_file.py` turns that into the simulator's
`<label>;<CLI args>` parameter file, deduplicating configurations that win more than one
race, and writing `--seed 12345` on every line — a configuration without a seed draws
`System.nanoTime()` and cannot be reproduced. It rebuilds each label from the arguments it
emits and refuses to write a file where the two disagree.

The A and B parameter files (`run-alg-parameters.txt.alg{a,b}`) are built the same way from
the per-algorithm races, passing `--out` the matching path.

### Final evaluation

First, you will need to compile the simulator using Maven:

```bash
mvn clean package
```

Point `INSTANCE_DIR` at the test instances (30 to 39) — either the per-size layout the
tuning step already uses (`$DATA_DIR/test/<size>/`) or a single flat directory holding every
size, both of which the script accepts — then run the whole final evaluation with one command
per strategy. The configurations come from the elite sets chosen in the previous step:

```bash
export INSTANCE_DIR=/mnt/instances/data/test
PARAM_FILE=$PWD/run-alg-parameters.txt.alga ./run-alg-full-on-all.sh > a_full.out 2> a_progress.log
PARAM_FILE=$PWD/run-alg-parameters.txt.algb ./run-alg-full-on-all.sh > b_full.out 2> b_progress.log
PARAM_FILE=$PWD/run-alg-parameters.txt.algc ./run-alg-full-on-all.sh > c_full.out 2> c_progress.log
```

`run-alg-full-on-all.sh` sweeps **every**
configuration in `PARAM_FILE` over all three instance sizes, all four capacities and test
instances 30-39, runs them in `STEPS_FULL` mode so each run also emits its per-step CSV,
and writes into `$RESULTS_DIR` (default `/mnt/instances/llls-results`):

```
.
├── small.log / medium.log / big.log      # Strategy C (all elites), the format the analysis reads
├── a_small.log / a_medium.log / …        # Strategy A, same format
├── b_small.log / b_medium.log / …        # Strategy B
├── small.tar / medium.tar / big.tar      # per-step CSVs, one zstd member per run
└── a_small.tar / b_big.tar / …           # likewise, prefixed per strategy
```

Each `.tar` archive holds one
`<run>.csv.zst` plus its `<run>.results.json`. No uncompressed CSV ever outlives its own
job, which is what makes the full grid feasible — unpacked it would be about 2.2 TB.

The grid is driven entirely from the environment, so the script never has to be edited:

| Variable                            | Default                       | Meaning                                                         |
| ----------------------------------- | ----------------------------- | --------------------------------------------------------------- |
| `PARAM_FILE`                        | `run-alg-parameters.txt.algc` | configurations to run; also sets the `a_`/`b_` output prefix    |
| `INSTANCE_TYPES`                    | `small medium big`            | instance sizes                                                  |
| `CAPACITIES`                        | `50 150 650 1000`             | media server capacities                                         |
| `INSTANCE_DIR`                      | `test-instances`              | instance CSVs, flat or one directory per size                   |
| `INSTANCE_IDS`                      | `30..39`                      | which test instances; a single id re-runs one sample instance   |
| `RESULTS_DIR`                       | `/mnt/instances/llls-results` | archives and logs                                               |
| `SCRATCH_DIR`                       | `/mnt/instances/llls-scratch` | per-job working directories                                     |
| `ZSTD_LEVEL`                        | `9`                           | archive compression level                                       |
| `MAX_JOBS_SMALL`, `MAX_JOBS_MEDIUM` | `63`                          | concurrent jobs; lower them on a smaller machine                |
| `MAX_JOBS_BIG`                      | `32`                          | same for big instances, which hold multi-GB CSVs while they run |

Progress goes to stderr with an ETA weighted by instance size; the ordered per-run output
goes to stdout. As with the tuning grid, **do not edit the script while it is running** — bash
reads it by byte offset as it executes.

**Plan for the disk.** The Strategy C grid alone is 457 GB of archives (small 3.7 GB,
medium 67 GB, big 386 GB); A adds about 15 GB (it is infeasible at low capacity, so many
runs produce no CSV at all) and B about 374 GB. The script pauses launching new jobs
whenever free space falls below `MIN_FREE_GB` (120 GB by default), because a big run holds
a multi-GB uncompressed CSV until it finishes and up to 32 of them run at once.

**Every configuration must carry `--seed`.** A strategy defaults its seed to
`System.nanoTime()` when the flag is absent, so a parameter file without `--seed` gives every
one of its runs a different random seed: the results are not reproducible, and configurations
are no longer compared on equal footing. All three `run-alg-parameters.txt.alg{a,b,c}` files
end each line with `--seed 12345`; keep that when adding configurations. The seed appears
in the output filename, which is the quickest way to check a run actually used it.

**`java` must be on `PATH`.** The script shells out to `java` directly, so a non-interactive
launch (`ssh host './run-alg-full-on-all.sh'`) that does not source the login profile will
fail every job instantly with `java: command not found`. Pass it explicitly, e.g.
`PATH=$JAVA_HOME/bin:$PATH ./run-alg-full-on-all.sh`.

The results of this step for the paper are collected in the `test_elite_configs.zip` file.

#### When the final evaluation stalls

Every finished job appends its members to one tar per instance size, serialised by a
`flock`. On GNU tar 1.32 and newer that append seeks to the end and costs nothing. On older
tar it **reads the entire archive first**, so each append costs a full scan and the grid
becomes quadratic in the number of runs. `run-alg-full-on-all.sh` warns at startup when it
finds tar older than 1.32.

The stall is worse than slow, because it also stops the grid from finishing: with every job
slot parked on the lock, `wait_for_slot` never returns, so the main loop stops launching.
In our run **the last 17 of 960 jobs were never started at all** — they are simply absent,
not failed, and nothing in the logs says so. Compare the run count in each `.log` against
`configurations × sizes × capacities × instances` before trusting a grid that stalled.

**Do not kill the queued jobs to unstick it.** `run_one` runs `rm -rf "$jobdir"` after the
tar call _regardless of its exit status_, so killing the `flock` waiters deletes those runs'
per-step CSVs. Recover in this order:

```bash
# 1. copy the pending results somewhere safe FIRST
mkdir -p ~/llls-rescue/jobs
for d in "$SCRATCH_DIR"/job.*; do [ -d "$d/results" ] && cp -a "$d/results" ~/llls-rescue/jobs/$(basename $d); done

# 2. stop the driver, then the waiters that are not holding the lock; let the
#    in-flight tar finish on its own so the archive is not left truncated
pkill -f 'run-alg-full-on-al[l].sh'
for pid in $(pgrep -f "flock $SCRATCH_DIR"); do pgrep -P "$pid" >/dev/null || kill "$pid"; done

# 3. run the jobs that never launched (JOBS entries are <grid index>:<size>:<capacity>:<instance>)
INSTANCE_DIR=$INSTANCE_DIR PARAM_FILE=$PWD/run-alg-parameters.txt.algc \
  CONFIG_LINE=12 OUT_DIR=~/llls-resume \
  JOBS="943:medium:650:33 944:medium:650:34 ..." ./run-alg-full-resume.sh

# 4. append everything in ONE tar call -- one scan instead of one per member
tar -rf "$RESULTS_DIR/medium.tar" -C ~/llls-append $(ls ~/llls-append)
```

Step 4 is the whole point: 47 members appended one at a time cost about an hour, and 2 m 25 s
in a single call.

`run-alg-full-resume.sh` re-runs a named subset of the grid exactly as the driver would, and
writes each job's legacy log entry as `legacy-<grid index>.txt` so the per-size `.log` can be
reassembled in grid order. The grid index is
`config × sizes × capacities × instances`, in the order the driver iterates them: for each
configuration, each instance size, each capacity, then instances 30-39.

Two things to check afterwards:

- **Append members by bare name.** `tar -rf archive -C dir sub/file` stores the member as
  `sub/file`, and `reduce_archive.py` parses the run's identity out of the member name and
  cannot read that. Stage the files flat (hard links are enough) and pass bare basenames.
- **Count the members.** `tar -tf medium.tar | grep -c csv.zst` must equal the number of runs,
  and `sort -u` on that list must give the same number — a duplicate is silently double-counted
  by the reduction.

#### The single-instance sample behind Table 6 and Figure 5

The paper's peak-concurrency table and its servers-in-use figure are drawn from **instance
30 only**, for the winning configuration of each of the three strategies. The Strategy C
runs already exist in the full grid above; the A and B ones are a small extra sweep, which
is why the simulator carries winner-only parameter files:

```bash
cd ../llls-simulator
for alg in a b; do
  INSTANCE_DIR=<test instances> \
  PARAM_FILE=$PWD/run-alg-parameters.winner.txt.alg$alg \
  INSTANCE_TYPES="small medium big" INSTANCE_IDS=30 \
  RESULTS_DIR=<results dir> ./run-alg-full-on-all.sh
done
```

That is 12 runs per strategy and a few minutes. Reduce the resulting `a_*.tar` / `b_*.tar`
alongside the C archives, as below, and `depth_analysis.py` and `steps_plots.py` pick all
three strategies up automatically.

### Reducing the per-step archives

The archives are too large to unpack, so the analysis never does. `reduce_archives.sh`
streams each run's CSV out of its tar by byte offset, decompresses it on the fly, takes its
statistics in a single pass and discards it:

```bash
./final_experimentation/reduce_archives.sh /mnt/instances/llls-results steps_full \
    small medium big a_small a_medium a_big b_small b_medium b_big
```

`NSHARDS` (default 48) parallel workers each hold one decompression pipe, so set it to
roughly the core count. This turns 457 GB into about 800 MB:

```
steps_full/
├── index/<archive>.jsonl        # member -> byte offset, so a run can be read without rescanning
├── summary/<archive>.jsonl      # one record per run (see below)
├── summary/<archive>/shard-*.jsonl   # the same records, before the shards are concatenated
└── series/<archive>/*.csv.gz    # one decimated per-step series per run
```

Each **summary** record carries the run's identity (instance size, capacity, instance,
strategy, and the configuration label that joins it to the evaluation logs) and, computed
exactly over every row of the CSV:

- `objective`, `max_servers`, `servers_created`, `max_sessions`, `max_viewers`;
- `max_tree_depth`, `max_avg_tree_depth` — the deepest session tree reached;
- `tw_mean_*`, `vw_mean_*` — depth averaged over the time each depth is held, and over
  viewer-seconds, so a one-second spike does not read like a steady state;
- `depth_time_hist`, `avg_depth_time_hist` — seconds held at each depth, which give exact
  time-weighted percentiles rather than sampled ones.

Each **series** file is the per-step CSV decimated by row index, with the stride chosen per run
so every series lands at 20k-40k rows regardless of instance size.

## Competitive ratio and the offline optimum

Section 5.7 of the paper compares the three online strategies against an offline optimum. Two
artifacts back it, and both are produced from the simulator source in `llls-simulator.zip`,
which carries the mixed-integer model alongside the strategies.

**Exact optima on tiny instances.** The optimum is only attainable on instances small enough
for a solver, so the study uses a replicated factorial design of 108 cells varying capacity,
number of concurrent sessions, load multiple, dwell-time distribution and churn. The instances
are generated by `SyntheticInstanceGenerator`, which is deterministic given its seed, so they
are regenerated by the study rather than shipped; the seeds are recorded alongside the design.

```bash
# in the unpacked simulator
mvn -q package -DskipTests
./model/experiments/run-competitive-ratio-study.sh --dry-run   # print the plan
./model/experiments/run-competitive-ratio-study.sh             # every stage, in order
```

The script is staged and resumable: a stage that finishes leaves a stamp in
`model/experiments/.rerun-state` and is skipped on relaunch, and the factorial stage resumes
cell by cell. `--restart` discards the stamps. Budget roughly 12 hours in total; the factorial
stage alone is about 6 hours at the default 300 s solve limit.

**LB0 bounds on the real instances.** The optimum cannot be computed for the instances derived
from FacebookVideosLive18, so the paper bounds it from below with LB0, a counting bound that
needs one pass over each instance and no solver. The `traces` stage of the same script computes
it for all 120 instances at the four capacities.

Both sets of results are version-controlled inside the simulator repository, so they ship in
`llls-simulator.zip` under `model/experiments/`:

| Path | Contents |
|---|---|
| `factorial/design.csv` | the 108 cells, with the parameters and the seed of each |
| `factorial/observations.csv` | one row per cell and strategy: cost, optimum, ratio, solve status |
| `factorial/cells.jsonl`, `factorial/summary.json` | per-cell solver detail and the aggregate |
| `real-traces/real-traces.csv` | one row per instance, capacity and strategy: LB0, cost, ratio upper bound |
| `real-traces/by-instance/` | the per-run detail behind each row |
| `*.md` | the reports interpreting the two studies |

The paper's Tables 4 and 5 are transcribed from `model/experiments/factorial/observations.csv`
and `model/experiments/real-traces/real-traces.csv` respectively.

## Per-hop latency on real media servers

The paper converts interconnection depth into milliseconds using a per-hop cost measured on
real media servers rather than assumed. The campaign ships as two archives:
`mediasoup-LLLS-experiments.zip` holds the testbed code and the analysis scripts, and
`hop_latency.zip` holds the per-run measurements and the fit outputs.

The testbed chains *N* mediasoup media servers, one per EC2 virtual machine, all in one AWS
region and forwarding between themselves over the provider's internal network. A browser
publishes a video whose frames carry a visible sequence number into the head of the chain and
subscribes from the tail; the screen is recorded, and the two sequence numbers visible at any
instant give the end-to-end latency of the whole chain. The campaign is 195 runs: 13 chain
lengths from 1 to 150 servers, three resolutions (480p, 720p, 1080p) and five repetitions,
with the chain lengths visited in a seeded random order and the browser in the same region as
the servers.

Re-running the campaign costs money and needs an AWS account; re-deriving the number from the
shipped measurements does not:

```bash
# fit the per-hop slope from the per-run measurements
python3 analysis/fit_hop_latency.py --results campaign_results \
    --min-fps 27 --out-prefix campaign_results/fit-final

# the robustness checks reported in the paper
python3 analysis/robustness_checks_v2.py \
    --runs campaign_results/fit-final_runs.csv \
    --manifest campaign_results/campaign-manifest.csv
```

This yields the per-hop cost the paper quotes, **0.091 ms/hop, 95 % CI [0.054, 0.127]**. Two
limits on how that number may be used, both established by the campaign itself: the slope is
only resolvable over long chains --- below roughly 60 hops the predicted increase is smaller
than the run-to-run variability of the measurement --- and it was identified over 0 to 149
hops, so any depth beyond that is extrapolation.

`hop_latency.zip` unpacks to `campaign_results/<resolution>/<N>_workers/try_<i>/`, one directory
per (resolution, chain length, repetition). Each holds the per-frame OCR output
(`ocr_results.csv`) and the WebRTC statistics of the run (`stats/`, one CSV per media server
plus `stats.json`); `recordings/` is empty, because the screen recordings are not in the package (see below).
Beside those directories:

| File | Contents |
|---|---|
| `campaign-manifest.csv` | one row per attempt: the seeded random visiting order (seed 20260828), status, start/finish times. It has 204 rows --- the 195 completed runs plus 9 attempts marked `failed` that were retried |
| `campaign.log` | the driver's log for the whole campaign |
| `fit-final.txt`, `fit-final_fits.csv`, `fit-final_runs.csv` | the fit's report, per-resolution fits and per-run medians |
| `fit-final_beta.tex`, `fit-final_rtt_vs_hops.png` | the per-hop cost as the paper quotes it, and its plot |

The screen recordings the OCR step reads (195 files, about 50 GB) are not part of the package,
because of Zenodo's size limit. They are available on request from the corresponding authors.
You only need them to re-run the OCR step; `hop_latency.zip` already carries its output.

With the recordings unpacked over the same `campaign_results/` tree, so each recording sits next
to the `ocr_results.csv` it produced, read the frame counters back out with `rtt_analyzer.py`. The
OCR crop rectangles differ per resolution because the frame counter sits at a different place in
each capture, so they have to be passed explicitly:

```bash
crops_for() {   # "<presenter rect> <viewer rect>", as left,top,right,bottom
    case "$1" in
        480p)  echo "905,526,1023,544 905,921,1023,939" ;;
        720p)  echo "902,525,1026,544 903,921,1026,939" ;;
        1080p) echo "903,525,1026,544 903,920,1026,939" ;;
    esac
}

for rec in campaign_results/*/*_workers/try_*/recordings/*.mp4; do
    try_dir=$(dirname "$(dirname "$rec")")
    [ -f "$try_dir/ocr_results.csv" ] && continue          # idempotent: skip what is done
    res=$(echo "$rec" | cut -d/ -f2)
    crops=$(crops_for "$res")
    python3 qoe_scripts/rtt_analyzer.py --video "$rec" \
        --ocr_presenter_coordinates "${crops%% *}" \
        --ocr_viewer_coordinates "${crops##* }" \
        --max_frame_count 3600 \
        --output "$try_dir/ocr_results.csv"
done
```

`--output` is not optional: `rtt_analyzer.py` writes beside the video by default, while
`fit_hop_latency.py` reads `try_<i>/ocr_results.csv`. `--max_frame_count` must match the media
--- 3600 for the 130 s files used in this campaign --- because readings above it are treated as
OCR errors. Budget roughly two minutes per recording, about 6.5 hours for the 195-run grid, and
run it on a machine that is not recording anything.

The testbed source itself is in `mediasoup-LLLS-experiments.zip`, and every command in this
section runs from its root. `analysis/collect_and_ocr.sh` does the same job during a live
campaign, pulling finished runs off the client as they complete, and needs `--host`; it is not
the path to use on an unpacked package.

## Analysis

Install the required Python packages with:

```bash
pip install -r requirements.txt
```

### Final experimentation (scripted)

The final-evaluation tables, the tree-depth analysis and the per-step figures are produced
by three scripts in `final_experimentation/`, which run from a shell and need neither R nor
Jupyter. Run them in this order — the second and third read the winners the first picks:

```bash
python3 final_experimentation/final_evaluation.py --logs test_elite_configs --out tables
python3 final_experimentation/depth_analysis.py   --summaries steps_full/summary --out tables
python3 final_experimentation/steps_plots.py      --series steps_full/series --out tables
```

`steps_plots.py` draws each figure as stacked panels by default — viewers, servers in use
and interconnection depth on a shared time axis — which is the clearer form to read on
screen. The manuscript's Figure 5 is the compact form: servers in use on the left axis and
viewer demand dashed on a twin right axis, one axes per instance size, windowed to hours
2–6:

```bash
python3 final_experimentation/steps_plots.py --out tables --instance 30 \
    --start 7200 --end 21600 --combined --suffix _paper
```

A twin axis lets a reader see a crossing point between two quantities that differ by three
orders of magnitude, so `--combined` is a deliberate concession to the page limit rather
than the better chart; the viewer curve is dashed and in a colour no strategy uses so it
does not read as a fourth strategy. `--no-depth-panel` gives the two-panel form in between.

| Script                | Reads                                                | Writes                                                                                                                                                       |
| --------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `final_evaluation.py` | `test_elite_configs/*.log`                           | `alt_results*.tex`, `winners_global.tex`, `alg_comparison_*.tex`, `costs_all.tex`, `best_elite_runs.csv`, `winners.json`                                     |
| `depth_analysis.py`   | `steps_full/summary/*.jsonl`, `best_elite_runs.csv`  | `max_simultaneous_servers.tex`, `tree_depth_by_strategy.tex`, `tree_depth_weighted.tex`, `depth_vs_cost.tex`, `depth_cost_tradeoff.tex`, `plots/depth_*.png` |
| `steps_plots.py`      | `steps_full/series/**`, `steps_full/summary/*.jsonl` | `plots/servers_in_use_*.png`, `plots/depth_over_time_*.png`                                                                                                  |

Four more scripts sit either side of that step. The first two run _before_ the final
evaluation and are described under
[Picking the elite configurations](#picking-the-elite-configurations-for-the-final-evaluation);
the last two run after it and write the manuscript's own hand-formatted tables, so they are
the only ones that write outside this repository:

| Script                    | Reads                                                                                       | Writes                                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `elite_configs.py`        | `irace_results/**/irace.Rdata`                                                              | `tables/elite_configs.csv`, `tables/best_elites.json`                                             |
| `elites_to_param_file.py` | `tables/best_elites.json`                                                                   | `run-alg-parameters.txt.alg*` in the simulator                                                    |
| `best_elites_table.py`    | `tables/best_elites.json`, `tables/best_elite_runs.csv`                                     | the manuscript's `tables/best_elites.tex`                                                         |
| `paper_eval_tables.py`    | `tables/winner_costs_per_instance.csv`, `tables/best_elite_runs.csv`, `steps_full/summary/` | the manuscript's `alg_compare_instance.tex`, `winners_global.tex`, `max_simultaneous_servers.tex` |

```bash
python3 final_experimentation/best_elites_table.py \
    --out ../webrtc-scalability-strategies/tables/best_elites.tex
python3 final_experimentation/paper_eval_tables.py \
    --out ../webrtc-scalability-strategies/tables
```

Both rebuild each configuration label from the values they emit and fail rather than write a
table whose label and parameters disagree, which is the failure a hand-transcribed table
cannot detect.

`final_evaluation.py` recomputes the winner of each strategy as the one with the lowest mean RPD. Rank and RPD are
defined as rank of the cost within each (instance size, capacity, instance)
group, and `100 * |cost - best| / best` within the same group, both then averaged across
groups. Infeasible Strategy A runs are excluded from the means and counted separately.

**Infeasible runs still leave a CSV.** A strategy that cannot serve every viewer exits
mid-simulation, by which point `StepSaver` has flushed whole buffers — so the archive holds a
CSV truncated at a multiple of `buffer.size` (10,000 rows). Reduced naively those look like
short, shallow runs and drag every average down, so `depth_analysis.py` and `steps_plots.py`
read `best_elite_runs.csv` and drop every run the evaluation log reports as an error. That is
why they must be run after `final_evaluation.py`.

### Interconnection depth in milliseconds

`depth_analysis.py` reports interconnection depth in media server levels. The paper reports it
in milliseconds, using the per-hop cost measured in
[Per-hop latency on real media servers](#per-hop-latency-on-real-media-servers). A session on a
single media server has depth 1, so a viewer on the deepest level of a tree of depth *d* is
*d-1* server hops from its publisher, and the latency that interconnection adds is
`beta * (depth - 1)` with `beta = 0.091` ms/hop.

Three scripts do the conversion. They read the winner of each strategy from `winners.json`, so
run `final_evaluation.py` first.

```bash
# distribution over aggregate viewing time -- needs only the reduced per-step data
python3 final_experimentation/viewer_latency_dist.py \
    --root steps_full --winners tables/winners.json --out tables

# distribution over VIEWERS, each counted once -- also needs the instance event streams
python3 final_experimentation/per_viewer_latency.py \
    --sizes small medium big --instances instances --out tables

# the manuscript's figure, drawn from the histograms the previous script writes
python3 final_experimentation/per_viewer_plot.py \
    --hists tables/per_viewer_hists.npz --out tables/plots
```

| Script | Reads | Writes |
| --- | --- | --- |
| `viewer_latency_dist.py` | `steps_full/{summary,series}`, `tables/winners.json` | `tables/viewer_latency_percentiles.txt`, `plots/viewer_latency_*.png` |
| `per_viewer_latency.py` | `instances/`, `steps_full/`, `tables/winners.json` | `tables/per_viewer_latency.txt`, `tables/per_viewer_hists.npz` |
| `per_viewer_plot.py` | `tables/per_viewer_hists.npz` | `plots/per_viewer_latency_violin.png` |

The difference between the first two is the weighting, and it is worth being explicit about
because they answer different questions. `viewer_latency_dist.py` weights each interval by
`viewers x dt`, so it reports how much of the aggregate *viewing time* is spent at each
latency. `per_viewer_latency.py` reconstructs every viewer's own join-to-leave interval from
the instance event stream, averages the depth over exactly that interval, and gives each viewer
one observation regardless of how long it stayed --- which is what "x % of viewers" means. The
paper reports the per-viewer form; the figure it prints (Figure 4) is
`plots/per_viewer_latency_violin.png`, copied into the manuscript as
`images/plots/per_viewer_latency.png`.

Two practical notes. `per_viewer_latency.py` streams roughly 22 GB of instance CSVs and takes
about 40 minutes, dominated by the big instances; it holds only the currently-connected viewers
in memory and accumulates results into fixed histograms, so its memory use does not grow with
the 899 million viewer observations it aggregates. And both scripts report a bracket rather
than a single number: the simulator records the depth of the session *trees*, not the level at
which each viewer attached, so the upper-bound row charges every viewer the deepest tree on the
platform while it was connected and the central-estimate row charges the mean depth over the
live trees. A viewer inside a tree of depth *d* actually sits between 0 and *d-1* hops down, so
the true distribution lies between the two rows and closer to the lower one.

### Notebooks

The notebooks are for exploratory analysis:

- `irace.ipynb`: analysis of the parameter evaluation step (and a notebook
  version of the final evaluation). It needs R 4.5 with irace 4.2.0 through `rpy2`, and
  expects:

```
.
├── irace_results/              # irace results from the parameter evaluation step
└── test_elite_configs/         # final evaluation logs
```

- `instances.ipynb`, `sessions.ipynb`: per-instance and per-session statistics of the
  generated FacebookLLLS dataset.
