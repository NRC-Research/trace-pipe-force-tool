import os

print("Starting GRAVEPlot batch comparison plotting script...")

# Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
net_forces_path = os.path.join(script_dir, "custom_forces.th")
fric_forces_path = os.path.join(script_dir, "custom_forces_friction_only.th")
output_png = os.path.join(script_dir, "forces_transient_comparison.png")
output_html = os.path.join(script_dir, "forces_transient_comparison.html")

# Helper function to parse .th files
def parse_th_file(filepath):
    xs = []
    ys = []
    with open(filepath, "r") as f:
        for line in f:
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
                    continue
    return xs, ys

# Load Net forces
print(f"Parsing net force data from {net_forces_path}...")
net_xs, net_ys = parse_th_file(net_forces_path)
net_dataset = build_dataset_from_lists(
    name="Net Dynamic Force (Bounded)",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=net_xs,
    ys=net_ys
)

# Load Friction forces
print(f"Parsing friction-only data from {fric_forces_path}...")
fric_xs, fric_ys = parse_th_file(fric_forces_path)
fric_dataset = build_dataset_from_lists(
    name="Wall Shear Force (Friction Only)",
    x_label="Time (s)",
    y_label="Force (N)",
    xs=fric_xs,
    ys=fric_ys
)

# Create a line plot
plot = build_line_plot("Piping Force Transient Comparison")
plot.add_datasets(net_dataset, fric_dataset)

# Style axes
plot.axis.x.label = "Time (s)"
plot.axis.y.label = "Force (N)"
plot.axis.x.scale = True
plot.axis.y.scale = True

# Style series
try:
    if len(plot.series) >= 2:
        # Series 0: Net Force (Red)
        s0 = plot.series[0]
        s0.line_color = "#d62728"  # Slate Red
        s0.line_width = 3.0
        s0.point_symbol = "circle"
        s0.symbol_size = 6
        
        # Series 1: Friction Force (Blue)
        s1 = plot.series[1]
        s1.line_color = "#1f77b4"  # Steel Blue
        s1.line_width = 3.0
        s1.point_symbol = "diamond"
        s1.symbol_size = 6
except Exception as e:
    print(f"Warning styling series: {e}")

# Render to PNG (static image) and HTML (interactive)
print(f"Rendering plot to {output_png}...")
plot.render(output_png, width=1200, height=800)

print("Finished comparison plotting successfully!")
