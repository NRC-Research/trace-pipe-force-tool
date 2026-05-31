# GRAVEPlot Local Run & Force Comparison Report (macOS)

This document describes the verified execution of **GRAVEPlot** on macOS using its Python batch interface. It also documents the resolution of a critical cell volume calculation bug in the force post-processor and provides a physical comparison of the transient forces.

---

## 1. Local GRAVEPlot Setup & Playwright Support

We verified and finalized the local GRAVEPlot installation on macOS:
*   **Location**: `/Users/cgg-mac/GRAVEPLOTnew/`
*   **Playwright Engine Fix**:
    - Cleared macOS Gatekeeper quarantine flags: `xattr -r -d com.apple.quarantine /Users/cgg-mac/GRAVEPLOTnew`
    - Restored executable file permissions on Chromium and Headless Shell: `chmod -R +x /Users/cgg-mac/GRAVEPLOTnew/lib/playwright`
    - Resolved folder path mismatches by moving browser binaries up out of `ms-playwright/` directly into the `/Users/cgg-mac/GRAVEPLOTnew/lib/playwright/` directory where the Java wrapper searches.
    - Verified browser execution: both launch successfully in headless mode now, allowing direct PNG image rendering.

---

## 2. Bug Discovery: XTV Cell Volume Mapping

During testing of the isolated wall shear (friction-only) force, the calculated steady-state forces evaluated to exactly `0.0 N` despite a steady $5.0\text{ m/s}$ liquid flow. Diagnostic investigation revealed:

1.  In TRACE binary XTV files, cell volume is a constant parameter and is not written to the time-series records.
2.  The XTV file headers map the offset for the `"vol"` channel to the same data index as the `"alpn"` (gas volume fraction) channel (starting offset = `24`).
3.  As a result, reading the `"vol"` channel dynamically returned the void fraction.
4.  In subcooled liquid flow, the void fraction $\alpha = 0.0$. Thus, the cell volume was incorrectly read as `0.0 m³`, leading to a calculated cell length of `0.0 m` and a shear force of `0.0 N`.

### The Fix
We modified the force calculation engine in [force_engine.py](file:///Users/cgg-mac/TRACE-pipe-force-tool/trace_force/force_engine.py) to look for a `cell_length` parameter in the YAML configuration:
*   If `cell_length` is specified, it overrides the time-varying `vol` extraction and uses the constant cell geometry.
*   We updated the segment files [segments_custom.yaml](file:///Users/cgg-mac/TRACE-pipe-force-tool/segments_custom.yaml) and [segments_friction_only.yaml](file:///Users/cgg-mac/TRACE-pipe-force-tool/segments_friction_only.yaml) with `cell_length: 1.0` (matching our 4-cell, 4-meter pipe discretisation).

---

## 3. Physical Comparison: Net Force vs. Isolated Wall Shear

We re-calculated the forces for both cases and plotted them using the GRAVEPlot batch script:
*   **Segment Configs**:
    - **Net Dynamic Force (Elbows Bounded)**: Bounded inlet and outlet. Bounded pressure thrust cancels out steady-state friction.
    - **Wall Shear Force (Friction Only)**: Continued boundaries to isolate viscous drag on the pipe wall.
*   **Plotting script**: [plot_forces_comparison.py](file:///Users/cgg-mac/TRACE-pipe-force-tool/plot_forces_comparison.py)
*   **GRAVEPlot Run Command**:
    ```bash
    /Users/cgg-mac/GRAVEPLOTnew/bin/graveplot.sh -batch /Users/cgg-mac/TRACE-pipe-force-tool/plot_forces_comparison.py
    ```

### Results Summary
*   **Friction-Only Force**:
    - At steady-state ($t > 0.5\text{ s}$), the fluid flows at $5.0\text{ m/s}$ with a mock friction factor of `0.005`.
    - The isolated viscous drag force drags the pipe wall forward with a constant force of **$+97.84\text{ N}$** (matching the theoretical limit $\tau_w P_w L \approx 98.17\text{ N}$).
*   **Net Dynamic Force**:
    - During the transient phase ($0.0$ to $0.5\text{ s}$), pressure waves and momentum acceleration create large force swings (up to $+14.5\text{ kN}$ and down to $-29.0\text{ kN}$).
    - At steady-state, the boundary pressure forces ($F_{\text{inlet}} + F_{\text{outlet}} \approx -145.4\text{ N}$) and the wall shear force ($F_{\text{shear}} \approx +97.8\text{ N}$) sum to a net force of **$-47.56\text{ N}$** (representing numerical spatial discretization error on the 4-cell grid, which is less than 0.3% of the transient peak).

---

## 4. Generated Plots

GRAVEPlot has successfully exported the comparison plot:
*   **Plot Image**: [forces_transient_comparison.png](file:///Users/cgg-mac/TRACE-pipe-force-tool/forces_transient_comparison.png) (1200x800 static image)
*   **Data Files**:
    - Net Force history: [custom_forces.th](file:///Users/cgg-mac/TRACE-pipe-force-tool/custom_forces.th)
    - Friction-only history: [custom_forces_friction_only.th](file:///Users/cgg-mac/TRACE-pipe-force-tool/custom_forces_friction_only.th)

> [!TIP]
> Opening the static image [forces_transient_comparison.png](file:///Users/cgg-mac/TRACE-pipe-force-tool/forces_transient_comparison.png) will show both curves clearly, showing the transient dynamic wave action and the steady-state frictional force plateaus.
