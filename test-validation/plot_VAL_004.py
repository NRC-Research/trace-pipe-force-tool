import os

print("Starting GRAVEPlot batch plotting script for VAL-004...")
script_dir = "/Users/cgg-mac/TRACE-pipe-force-tool/test-validation"
val004_path = os.path.join(script_dir, "VAL_004.th")
val004_png = os.path.join(script_dir, "VAL_004_bend.png")

val004_times = []
val004_forces_x = []
val004_forces_y = []
with open(val004_path, "r") as f:
    for line in f:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                val004_times.append(float(parts[0]))
                val004_forces_x.append(float(parts[1]))
                val004_forces_y.append(float(parts[2]))
            except ValueError:
                continue

ds_val004_x = build_dataset_from_lists(
    name="VAL_004_SegX (Horizontal)",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=val004_times,
    ys=val004_forces_x
)
ds_val004_y = build_dataset_from_lists(
    name="VAL_004_SegY (Vertical)",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=val004_times,
    ys=val004_forces_y
)
plot = build_line_plot("Case VAL-004: 90-degree Elbow Forces")
plot.add_datasets(ds_val004_x, ds_val004_y)
plot.axis.x.label = "Time (s)"
plot.axis.y.label = "Force (N)"
plot.axis.x.scale = True
plot.axis.y.scale = True

if len(plot.series) >= 2:
    s0 = plot.series[0]
    s0.line_color = "#d62728"  # Slate Red
    s0.line_width = 3.0
    s1 = plot.series[1]
    s1.line_color = "#9467bd"  # Purple
    s1.line_width = 3.0

print(f"Rendering VAL-004 plot to {val004_png}...")
plot.render(val004_png, width=1200, height=800)
print("Finished VAL-004 plotting successfully!")
