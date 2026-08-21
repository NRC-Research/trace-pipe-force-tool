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

## 2. Generating Configuration Skeletons (`trace_force_make_config.py`)

For large plant-sized models with many pipes, writing the YAML configuration file from scratch can be tedious. A helper utility is provided to scan a TRACE XTV file, locate all `pipe` components, and automatically generate a template YAML file populated with their component IDs and cell lists:

### Usage
```bash
./trace_force_make_config.py -i <path_to_xtv> [-o <output_yaml>]
```
* `-i`, `--input`: Path to the TRACE binary XTV file.
* `-o`, `--output`: *(Optional)* Path to write the output skeleton YAML (defaults to `template_segments.yaml`).

### Example
```bash
./trace_force_make_config.py -i custom_flow.xtv -o segments.yaml
```
Once generated, you only need to review and update the `direction_vector`, `cell_length`, and connection junctions to match your plant layout.

---

## 3. Configuration Format (`segments.yaml`)


Define your settings and pipe segments using a YAML configuration:
```yaml
settings:
  ambient_pressure_pa: 101325.0
  units: "METRIC"          # METRIC (Newton) or BRITISH (lbf)
  output_format: "TH"      # TH (space-separated ASCII) or FRC (CSV for CAESAR II)

segments:
  - name: "Test_Segment_1"            # Letters, digits, _ and - only; becomes a column heading
    direction_vector: [0.0, 0.0, 1.0]  # Flow direction: must be a UNIT vector
    components:
      - id: 108
        type: "pipe"
        cells: [1, 2, 3, 4, 5, 6, 7, 8]  # Cell indices inside component
        cell_length: 0.5                 # Required: length of each cell (m), > 0
    inlet_junction:
      type: "BOUNDED"      # CONTINUED, BOUNDED, or OPEN
    outlet_junction:
      type: "OPEN"
      area: 0.004          # OPEN only: junction flow area A_j (m^2), > 0
```

Junction types and the boundary force they contribute:

* **`BOUNDED`** — a closed end. Full pressure-plus-momentum thrust on the cell flow area.
* **`CONTINUED`** — the segment continues into further piping. No boundary force.
* **`OPEN`** — an open end. The force acts on the annular lip `(A_cell - A_j)` left where
  the opening is smaller than the cell bore, with `A_j` given by `area`. Omitting `area`
  means a plain open exit: `A_j = A_cell`, zero lip, zero force. Jet reaction (thrust) is
  not computed. `area` is only accepted on `OPEN` junctions — on the other types it would
  have no effect and is rejected.

---

## 3a. Running the tests

Configuration validation is covered by a small test suite that needs no packages
beyond the tool's own dependencies:

```bash
python3 -m unittest discover -s tests
```

The cases are ordinary `unittest.TestCase` classes, so `pytest` collects them
unchanged if you prefer to run them that way.

---

## 4. Elbow Roughness Calculator (`trace_roughness.py`)

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


## 5. Verification & Validation (V&V) Case Status

The post-processing engine has been verified against standard fluid-dynamics cases. Complete documentation of the tests is available in the [validation_test_plan.md](test-validation/validation_test_plan.md) and [validation_results_report.md](test-validation/validation_results_report.md).

### V&V Results Summary Matrix

| Case ID | Case Name | Primary Physics Tested | Target Force | Calculated Force | Status |
|---|---|---|---|---|---|
| **VAL-001** | Steady-State Pipe Friction | Viscous wall shear, discretization | $+2000.7\text{ N}$ (Shear) | $+2003.26\text{ N}$ | **PASSED** (+0.13%) |
| **VAL-002** | Acoustic Wave / Water Hammer | Transient wave shock propagation | $+392,699.08\text{ N}$ (Peak) | $+441,668.44\text{ N}$ | **PASSED** (+12.47% overshoot) |
| **VAL-003** | Piping Area Discontinuity | Step contraction momentum change | $-275,298.74\text{ N}$ (Static) | $-275,180.78\text{ N}$ | **PASSED** (-0.04%) |
| **VAL-004** | 90-degree Piping Bend | Directional vector projection | $F_x = -375,851.2\text{ N}$<br>$F_y = +375,338.3\text{ N}$ | $F_x = -375,862.63\text{ N}$<br>$F_y = +375,325.24\text{ N}$ | **PASSED** (0.003%) |
| **VAL-005** | EPRI Safety/Relief Valve | Two-phase transient dynamic loads | R5FORCE benchmark | TBD | **PLANNED** |

---

## 6. SNAP GUI Integration (Job Stream Step)

This post-processing tool is integrated into the **SNAP** (Symbolic Nuclear Analysis Program) GUI via the **SNAP Dynamic Piping Force Plugin**, allowing users to define segments and run post-processing calculations directly inside a SNAP Job Stream.

### Key Plugin Features:
* **JEdit-Free Swing YAML Editor**: Features an interactive Swing editor utilizing `RSyntaxTextArea` with syntax highlighting. Analysts can configure segments by clicking **"Edit YAML..."** directly in the step's property sheet, with options to edit in-app or open in a system editor (e.g. VS Code).
* **Custom Step Icon**: Integrates a clean, transparent, light-themed engineering icon (`force16.png`) in the SNAP Job Stream pallet.
* **Integrated Output Viewer**: Maps the output `forces.th` (`TH` type ASCII files) under the "Text Files" section in SNAP Job Status for direct viewing using the built-in text viewer.
* **Portable Dynamic Path Resolution**: Dynamically resolves the SNAP `python/` directory path relative to the plugin JAR's location (falling back to `CAFEAN_HOME`), eliminating local path hardcoding.

### Deployment & Distribution:
1. Download the compiled plugin package `trace-force-plugin-v1.0.0.zip` from the [Standalone GitHub Release](https://github.com/NRC-Research/SNAP-Distribution/releases/tag/trace-force-v1.0.0).
2. Extract the archive into the main SNAP directory (e.g., `<SNAP_HOME>/`).
3. Restart SNAP and the Calculation Server to enable the Piping Force calculation step.

### Working Example:
[`test-validation/VAL_004.med`](test-validation/VAL_004.med) contains a configured job stream for the VAL-004 bend case — TRACE run → Piping Force step → AptPlot — with its segment configuration in [`test-validation/segments_VAL_004-b.yaml`](test-validation/segments_VAL_004-b.yaml) (the same two-segment bend decomposition as `segments_VAL_004.yaml`; the step passes `--mock-friction 0.005` because the VAL-004 TRACE run does not write `wfl`/`wfv`). Run through the CLI, that configuration reproduces the committed `VAL_004.th` exactly.




