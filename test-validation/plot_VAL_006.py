import os

print("Starting GRAVEPlot batch plotting script for VAL-006...")
# The GRAVEPlot batch runner does not define __file__; fall back to the
# working directory (run from test-validation/ in that case).
script_dir = (os.path.dirname(os.path.abspath(__file__))
              if "__file__" in globals() else os.getcwd())
val006_path = os.path.join(script_dir, "VAL_006.th")
val006_png = os.path.join(script_dir, "VAL_006_gravity.png")

val006_times = []
val006_forces_incline = []
val006_forces_vertical = []
with open(val006_path, "r") as f:
    for line in f:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                val006_times.append(float(parts[0]))
                val006_forces_incline.append(float(parts[1]))
                val006_forces_vertical.append(float(parts[2]))
            except ValueError:
                continue

ds_val006_v = build_dataset_from_lists(
    name="VAL_006_SegVertical (target -rho*g*V = -7682.25 N)",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=val006_times,
    ys=val006_forces_vertical
)
ds_val006_i = build_dataset_from_lists(
    name="VAL_006_SegIncline30 (target -rho*g*V*sin30 = -3841.13 N)",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=val006_times,
    ys=val006_forces_incline
)
plot = build_line_plot("Case VAL-006: Static Column Gravity Forces")
plot.add_datasets(ds_val006_v, ds_val006_i)
plot.axis.x.label = "Time (s)"
plot.axis.y.label = "Force (N)"
plot.axis.x.scale = True
plot.axis.y.scale = True

if len(plot.series) >= 2:
    s0 = plot.series[0]
    s0.line_color = "#1f77b4"  # Blue
    s0.line_width = 3.0
    s1 = plot.series[1]
    s1.line_color = "#2ca02c"  # Green
    s1.line_width = 3.0

print(f"Rendering VAL-006 plot to {val006_png}...")
plot.render(val006_png, width=1200, height=800)
print("Finished VAL-006 plotting successfully!")
