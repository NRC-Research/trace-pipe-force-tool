import os

print("Starting GRAVEPlot batch plotting script for VAL-005 wet case...")
# The GRAVEPlot batch runner does not define __file__; fall back to the
# working directory (run from test-validation/ in that case).
script_dir = (os.path.dirname(os.path.abspath(__file__))
              if "__file__" in globals() else os.getcwd())
th_path = os.path.join(script_dir, "VAL_005W.th")
png_full = os.path.join(script_dir, "VAL_005W_srv.png")
png_zoom = os.path.join(script_dir, "VAL_005W_cf203_zoom.png")

cols = {}
with open(th_path, "r") as f:
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
cf203 = cols["VAL_005_CF203_DownLeg"]
cf201 = cols["VAL_005_CF201_Riser"]
tail = cols["VAL_005_Tail_OpenEnd"]

# Full-window plot: the Figure 15 analog (CF203 near-zero mean, slug burst,
# reclosure ripple), plus the legs carrying the big transit spikes.
ds1 = build_dataset_from_lists(
    name="CF203 DownLeg (ref Fig 15: burst 0.25-0.45 s, near-zero mean)",
    x_label="Time (s)", y_label="Force (N)", xs=times, ys=cf203)
ds2 = build_dataset_from_lists(
    name="CF201 Riser",
    x_label="Time (s)", y_label="Force (N)", xs=times, ys=cf201)
ds3 = build_dataset_from_lists(
    name="Tail leg with OPEN discharge end",
    x_label="Time (s)", y_label="Force (N)", xs=times, ys=tail)

plot = build_line_plot("Case VAL-005 Phase 2: Loop-Seal Blowdown Leg Forces")
plot.add_datasets(ds1, ds2, ds3)
plot.axis.x.label = "Time (s)"
plot.axis.y.label = "Force (N)"
plot.axis.x.scale = True
plot.axis.y.scale = True
for series, color in zip(plot.series, ["#2ca02c", "#1f77b4", "#9467bd"]):
    series.line_color = color
    series.line_width = 2.0
print(f"Rendering full-window plot to {png_full}...")
plot.render(png_full, width=1400, height=900)

# Close-up: the Figure 17 analog (CF203, slug-transit window). The reference
# window is 0.2-0.5 s; ours extends to 0.55 because the throttled slug
# arrives at the down leg ~0.1 s later.
zt = [ti for ti in times if 0.2 <= ti <= 0.55]
zf = [fi for ti, fi in zip(times, cf203) if 0.2 <= ti <= 0.55]
dsz = build_dataset_from_lists(
    name="CF203 DownLeg, slug transit (ref Fig 17: ~-40 kN deepest, ~+25 kN)",
    x_label="Time (s)", y_label="Force (N)", xs=zt, ys=zf)
plot2 = build_line_plot("Case VAL-005 Phase 2: CF203 During Slug Transit")
plot2.add_datasets(dsz)
plot2.axis.x.label = "Time (s)"
plot2.axis.y.label = "Force (N)"
plot2.axis.x.scale = True
plot2.axis.y.scale = True
plot2.series[0].line_color = "#2ca02c"
plot2.series[0].line_width = 2.5
print(f"Rendering close-up plot to {png_zoom}...")
plot2.render(png_zoom, width=1400, height=900)
print("Finished VAL-005 wet-case plotting successfully!")
