import os

print("Starting GRAVEPlot batch plotting script for VAL-005...")
# The GRAVEPlot batch runner does not define __file__; fall back to the
# working directory (run from test-validation/ in that case).
script_dir = (os.path.dirname(os.path.abspath(__file__))
              if "__file__" in globals() else os.getcwd())
val005_path = os.path.join(script_dir, "VAL_005.th")
val005_png = os.path.join(script_dir, "VAL_005_srv.png")

times = []
cols = {}
with open(val005_path, "r") as f:
    header = None
    for line in f:
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split()
        if header is None:
            header = parts
            for name in header:
                cols[name] = []
            continue
        for name, value in zip(header, parts):
            cols[name].append(float(value))

times = cols["Time(s)"]
cf201 = cols["VAL_005_CF201_Riser"]
cf202 = [a + b for a, b in zip(cols["VAL_005_CF202a_Header"],
                               cols["VAL_005_CF202b_LongRun"])]
cf203 = cols["VAL_005_CF203_DownLeg"]
tail = cols["VAL_005_Tail_OpenEnd"]

ds1 = build_dataset_from_lists(
    name="CF201 Riser (ref +4242/-5457 N at 1.43/1.46 s)",
    x_label="Time (s)", y_label="Force (N)", xs=times, ys=cf201)
ds2 = build_dataset_from_lists(
    name="CF202 Header+LongRun (ref -42420 N at 0.248 s)",
    x_label="Time (s)", y_label="Force (N)", xs=times, ys=cf202)
ds3 = build_dataset_from_lists(
    name="CF203 DownLeg (ref -6963 N at 0.247 s)",
    x_label="Time (s)", y_label="Force (N)", xs=times, ys=cf203)
ds4 = build_dataset_from_lists(
    name="Tail leg with OPEN discharge end",
    x_label="Time (s)", y_label="Force (N)", xs=times, ys=tail)

plot = build_line_plot("Case VAL-005 Phase 1: S/RV Blowdown Leg Forces")
plot.add_datasets(ds1, ds2, ds3, ds4)
plot.axis.x.label = "Time (s)"
plot.axis.y.label = "Force (N)"
plot.axis.x.scale = True
plot.axis.y.scale = True

colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]
for series, color in zip(plot.series, colors):
    series.line_color = color
    series.line_width = 2.0

print(f"Rendering VAL-005 plot to {val005_png}...")
plot.render(val005_png, width=1400, height=900)
print("Finished VAL-005 plotting successfully!")
