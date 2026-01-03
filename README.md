# WebRTC media server interconnection strategies for scalable low-latency live streaming sessions

Reproduction package for the paper "WebRTC media server interconnection strategies for scalable low-latency live streaming sessions". This description contains detailed steps to reproduce the results on the paper.

The complete reproduction package can be found in Zenodo ([https://doi.org/10.5281/zenodo.17779884](https://doi.org/10.5281/zenodo.17779884)) and contains the following files:
```
.
├── instance-generation.zip         # Scripts used to generate random instances based on FacebookVideosLive18 dataset
├── instances.zip                   # Instances generated for the experiments in the paper, separated by instance size.
├── llls-simulator.zip              # Simulator source code
├── irace_results.zip               # irace results from the experiments in the paper (parameter evaluation results)
├── test_elite_configs.zip          # Final evaluation results
├── simulated.zip                   # Detailed simulation results used for plotting in the paper
├── analysis.zip                    # Jupyter Notebooks for data analysis
└── README.md                       # This file
```

Note: in instances.zip, instances with numbers 0 to 29 correspond to the training set, and instances with numbers 30 to 39 correspond to the test set.
This repository contains scripts and notebooks for analyzing live video data from FacebookVideosLive18 dataset and generating pseudo randomized instances for simulation.

## Index

- [Requirements](#requirements)
- [Instance generation](#instance-generation)
- [Simulation](#simulation)
  - [Parameter evaluation](#parameter-evaluation)
  - [Final evaluation](#final-evaluation)
  - [Detailed data for analysis](#detailed-data-for-analysis)
- [Analysis](#analysis)

## Requirements

The following software versions were used:

For generating instances:
- Ubuntu 22.04
- Python 3.11

For running the simulations:
- Ubuntu 22.04
- Docker
- Java 21
- Maven 3.9

For data analysis:
- Windows 10 (Ubuntu 22.04 can also be used)
- Python 3.11
- R 4.5.0 with irace 4.2.0 package installed

## Instance generation
You can generate instances using the scripts in `instance_generation.zip`, or use the already generated instances in `instances.zip`. `instance_generation.zip` contains a folder with the necessary scripts for generating new instances. The scripts rely on the FacebookVideosLive18 dataset. The dataset can be downloaded from [here](https://sites.google.com/view/facebookvideoslive18/download?authuser=0). Ensure the datasets are placed in the `data/` directory (download both datasets' full compressed files and unzip them in `data/`). The directory structure should look like this:

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
pip install -r instance_generation/requirements.txt
python3 instance_generation/generate_instances.py
```

The instances will be generated in the `instances/` folder, separated by instance size. A `sessions/` folder will also be created containing the session data used for generating the instances.

## Simulation

The simulation source code is in `llls-simulator.zip`. Unzip the file.

### Parameter evaluation
To evaluate different parameter configurations using irace, add the instances of the same instance size (newly generated or from `instances.zip`) to the `instances/` folder. The paper used instances 0 to 29 of each instance size for this step. Don't mix instance sizes in the same run.

To run the irace experiments, run the following script:

```bash
./run-docker.sh
```

This will run the irace experiments as described in the paper. This process has to be done for each instance size you want results from. The results will be stored in the `tuning/` directory, where the results will be stored in each subdirectory as `irace.Rdata` files. Warning: `irace.Rdata` files can be overwritten if the script is run multiple times, so make sure to back them up.

Note: The process may take several hours to days depending on the instance size and computational resources. To improve running times, consider changing the `parallel` value in the `scenario.txt` files in each subdirectory of `tuning/` to the number of available CPU cores.

The results of this step for the paper are collected in the `irace_results.zip` file.

### Final evaluation

First, you will need to compile the simulator using Maven:

```bash
mvn clean package
```

To run the final evaluation using the best configurations found in the parameter evaluation step, add the test instances of a single instance size (instances 30 to 39) to the `instances/instances-<size>/` folder, where \<size\> is the size of the instances, "small", "medium" or "big" (remember to remove training instances if present).

For each instance size and algorithm, the script `run-alg-on-all.sh` will need to be run. Before running the script, make sure to edit the variable `INSTANCE_TYPE` in the script to the desired instance size ("small", "medium" or "big").

For each algorithm there is a parameters file next to the script called `run-alg-parameters.txt.algx` where `x` is the algorithm letter (a, b or c). Edit the variable `PARAM_FILE` in the script to point to the desired algorithm parameters file (the `.algc` file can be also understood as all algorithms, as C always wins).

Then, run the script:

```bash
./run-alg-on-all.sh > name.log
```

Where `name.log` is the desired name for the log file. For later analysis, it is recommended to name the log files with the following names for each algorithm:

```
.
├── a_big.log      # Algorithm A on big instances
├── a_medium.log   # Algorithm A on medium instances
├── a_small.log    # Algorithm A on small instances
├── b_big.log      # Algorithm B on big instances
├── b_medium.log   # Algorithm B on medium instances
├── b_small.log    # Algorithm B on small instances
├── c_big.log      # All algorithms (C) on big instances
├── c_medium.log   # All algorithms (C) on medium instances
└── c_small.log    # All algorithms (C) on small instances
```

The results of this step for the paper are collected in the `test_elite_configs.zip` file.

### Detailed data for analysis

In order to obtain the plots shown in the paper (and more), for any simulation run that you need detailed data for analysis you will need to use the `run-alg-full.sh` script. This script works similarly to `run-alg-on-all.sh`, but it will generate detailed output files for each simulation run in the `results/` folder. The output files will be CSV files with the data for each step of the simulation. Note that the script will only run the algorithms and configurations in the `PARAM_FILE` variable for a single instance (number identified in the variable `INSTANCE_ID`), so you will need to run the script multiple times for each instance you want detailed data for. For the paper, instance 30 from small instances was used.

The results of this step for the paper are collected in the `simulated.zip` file.

## Analysis
The analysis notebooks are in `analysis.zip`. Unzip the file and install the required Python packages with:

```bash
pip install -r requirements.txt
```

The following notebooks are available for analysis:

- irace.ipynb: Analysis of the parameter evaluation step and the final evaluation. It expects the data in the following structure:
```
.
├── irace_results/              # Folder containing the irace results from parameter evaluation step (download and check irace_results.zip for the expected structure)
└── test_elite_configs/         # Folder containing the final evaluation results (download and check test_elite_configs.zip for the expected structure)
```
- simulated.ipynb: Analysis of the detailed simulation results. It expects the data in the following structure:
```
.
└── simulated/                  # Folder containing the detailed simulation results (download and check simulated.zip for the expected structure)
```