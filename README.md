# TRACE Piping Force Calculation Tool (`trace_force`)

This repository contains a Python post-processing tool designed to calculate fluid-induced dynamic forces on piping segments using transient outputs from the **TRACE** system thermal-hydraulic code.

The calculations are based on the **Watkins Pressure-Shear Formulation** which integrates the fluid momentum conservation equations to calculate dynamic forces directly from wall shear and pressure terms, avoiding noisy numerical derivatives.

---

## Installation & Prerequisites

This tool requires Python 3.11+ and the following packages:
* **`standard-xdrlib`** (backport of `xdrlib` removed in Python 3.13+)
* **`pyyaml`** (YAML configuration parser)

To install the packages:
```bash
pip3 install --user standard-xdrlib pyyaml --break-system-packages
```

---

## 1. Force Calculator (`trace_force.py`)

Calculates dynamic force histories over specified pipe segments.

### Usage
```bash
./trace_force.py -i <path_to_xtv> -c <path_to_config_yaml> -o <path_to_output_file>
```
* `-i`, `--input`: Path to the TRACE binary XTV file.
* `-c`, `--config`: Path to the YAML configuration file defining the pipe segments.
* `-o`, `--output`: Path to write the output force time-history file (supports `.th` or `.frc`).
* `--mock-friction <val>`: *(Optional)* Bypasses the wall friction factor check and uses a constant mock value (e.g. `0.005`) for testing standard XTV runs.

### Example
```bash
./trace_force.py -i TypPWR.xtv -c segments.yaml -o forces.th --mock-friction 0.005
```

---

## 2. Configuration Format (`segments.yaml`)

Define your settings and pipe segments using a YAML configuration:
```yaml
settings:
  ambient_pressure_pa: 101325.0
  units: "METRIC"          # METRIC (Newton) or BRITISH (lbf)
  output_format: "TH"      # TH (space-separated ASCII) or FRC (CSV for CAESAR II)

segments:
  - name: "Test_Segment_1"
    direction_vector: [0.0, 0.0, 1.0]  # Flow direction vector (Z-axis)
    components:
      - id: 108
        type: "pipe"
        cells: [1, 2, 3, 4, 5, 6, 7, 8]  # Cell indices inside component
    inlet_junction:
      type: "BOUNDED"      # CONTINUED, BOUNDED, or OPEN
    outlet_junction:
      type: "BOUNDED"
```

---

## 3. Elbow Roughness Calculator (`trace_roughness.py`)

A utility to convert localized form losses (like elbow $K$-factors) into equivalent volume roughness values ($\epsilon_e$) for your TRACE input decks. This allows TRACE to natively incorporate form losses into its wall drag terms, which are then used by the force tool.

### Usage
```bash
./trace_roughness.py -k <total_elbow_K> -r <nominal_roughness> -l <cell_length> -d <cell_diameter>
```
* `-k`, `--k-factor`: Total elbow loss coefficient $K$ (automatically splits half upstream and half downstream).
* `-r`, `--roughness`: Pipe wall nominal roughness (m or ft).
* `-l`, `--length`: Adjacent volume cell length.
* `-d`, `--diameter`: Adjacent volume cell diameter.

### Example (from R5FORCE Manual Appendix C)
```bash
./trace_roughness.py -k 0.22512 -r 0.00015 -l 1.60 -d 0.6651
```

---

## 4. Running TRACE Locally via Lima & Apptainer

To run TRACE calculations locally on an Apple Silicon (ARM64) Mac, you can use the downloaded container image `trace-V5.1831.1-linux_aarch64-gfortran.sif` inside the `apptainer` Lima VM.

A helper script `run_trace.sh` has been provided in the workspace. It automatically starts the Lima VM (if it is stopped) and runs the TRACE SIF container with any arguments you pass to it.

### Usage
```bash
./run_trace.sh [TRACE_arguments...]
```

### Example
To verify TRACE runs and prints its version:
```bash
./run_trace.sh --version
```

To run a simulation (reads/writes files in the current folder):
```bash
./run_trace.sh -p my_model
```

