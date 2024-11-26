# Facebook Videos Live 18 Analysis

# Facebook Videos Live 18 Analysis

This repository contains scripts and notebooks for analyzing live video data from FacebookVideosLive18 dataset and generating pseudo randomized instances for simulation. The dataset can be downloaded from [here](https://sites.google.com/view/facebookvideoslive18/download?authuser=0).

## Project Structure

- `instance_generation/`
    - `generate_instances.py`: Script to generate instances from the dataset.
    - `generate_instances_tasks.py`: Contains functions for processing viewer data using Ray.

- `data/`
    - Contains datasets for June-July and January-February periods.

- `analysis.ipynb`
    - Jupyter notebook for analyzing the live video data.

- `instances.ipynb`
    - Jupyter notebook for analyzing the generated session types and instances.

## Requirements
Install the required packages using:
```sh
pip install -r requirements.txt
```

## Usage

1. **Data Preparation**: Ensure the datasets are placed in the `data/` directory (download both datasets' full compressed files and unzip them in `data/`).
2. **Instance Generation**: Run the script in the `instance_generation/generate_instances.py` directory to generate instances. Use argument `-h` to get help on its options.
3. **Analysis**: Use the Jupyter notebooks `analysis.ipynb` and `instances.ipynb` to perform data analysis and visualization.
