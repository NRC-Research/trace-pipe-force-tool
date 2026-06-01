import os

print("Starting GRAVEPlot batch plotting script for VAL-002...")
script_dir = os.path.dirname(os.path.abspath(__file__))
val002_path = os.path.join(script_dir, "VAL_002.th")
val002_png = os.path.join(script_dir, "VAL_002_acoustic_wave.png")

val002_times = []
val002_forces = []
with open(val002_path, "r") as f:
    for line in f:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                val002_times.append(float(parts[0]))
                val002_forces.append(float(parts[1]))
            except ValueError:
                continue

ds_val002 = build_dataset_from_lists(
    name="VAL_002_Acoustic_Wave (20-cell pipe)",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=val002_times,
    ys=val002_forces
)
plot = build_line_plot("Case VAL-002: Acoustic Wave Dynamic Force")
plot.add_dataset(ds_val002)
plot.axis.x.label = "Time (s)"
plot.axis.y.label = "Force (N)"
plot.axis.x.scale = True
plot.axis.y.scale = True

if len(plot.series) > 0:
    s0 = plot.series[0]
    s0.line_color = "#1f77b4"  # Steel Blue
    s0.line_width = 3.0

print(f"Rendering VAL-002 plot to {val002_png}...")
plot.render(val002_png, width=1200, height=800)
print("Finished VAL-002 plotting successfully!")
