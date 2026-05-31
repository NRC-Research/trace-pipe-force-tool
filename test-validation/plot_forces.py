import os

print("Starting GRAVEPlot batch plotting script...")

# Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
forces_path = os.path.join(script_dir, "custom_forces.th")
output_path = os.path.join(script_dir, "forces_transient.png")

# Parse the forces file in Python
print(f"Parsing force data from {forces_path}...")
xs = []
ys = []

with open(forces_path, "r") as f:
    for line in f:
        # Skip comment lines and empty lines
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                x = float(parts[0])
                y = float(parts[1])
                xs.append(x)
                ys.append(y)
            except ValueError:
                # Skip header row (e.g. "Time(s)  Custom_Pipe_Segment")
                continue

print(f"Parsed {len(xs)} data points.")

# Build the dataset from parsed lists
force_dataset = build_dataset_from_lists(
    name="Custom_Pipe_Segment",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=xs,
    ys=ys
)

print(f"Dataset successfully created: {force_dataset.name}")

# Build the line plot
plot = build_line_plot("Piping Dynamic Force Transient")
plot.add_dataset(force_dataset)

# Style axes
plot.axis.x.label = "Time (s)"
plot.axis.y.label = "Force (N)"
plot.axis.x.scale = True
plot.axis.y.scale = True

# Style series
if len(plot.series) > 0:
    s0 = plot.series[0]
    s0.line_color = "#d62728"  # Slate red
    s0.line_width = 3.0
    s0.point_symbol = "circle"
    s0.symbol_size = 6

# Render plot to image
print(f"Rendering plot to {output_path}...")
plot.render(output_path, width=1200, height=800)

print("Finished plotting successfully!")
