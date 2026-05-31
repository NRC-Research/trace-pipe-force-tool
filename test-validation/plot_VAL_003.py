import os

print("Starting GRAVEPlot batch plotting script for VAL-003...")
script_dir = "/Users/cgg-mac/TRACE-pipe-force-tool/test-validation"
val003_path = os.path.join(script_dir, "VAL_003.th")
val003_png = os.path.join(script_dir, "VAL_003_contraction.png")

val003_times = []
val003_forces = []
with open(val003_path, "r") as f:
    for line in f:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                val003_times.append(float(parts[0]))
                val003_forces.append(float(parts[1]))
            except ValueError:
                continue

ds_val003 = build_dataset_from_lists(
    name="VAL_003_Discontinuity",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=val003_times,
    ys=val003_forces
)
plot = build_line_plot("Case VAL-003: Step Contraction Force Balance")
plot.add_dataset(ds_val003)
plot.axis.x.label = "Time (s)"
plot.axis.y.label = "Force (N)"
plot.axis.x.scale = True
plot.axis.y.scale = True

if len(plot.series) > 0:
    s0 = plot.series[0]
    s0.line_color = "#2ca02c"  # Forest Green
    s0.line_width = 3.0

print(f"Rendering VAL-003 plot to {val003_png}...")
plot.render(val003_png, width=1200, height=800)
print("Finished VAL-003 plotting successfully!")
