import os

class OutputWriter:
    def __init__(self, app_config, times, results):
        self.app_config = app_config
        self.times = times
        self.results = results
        self.units = "lbf" if app_config.units == "BRITISH" else "N"

    def write_th_file(self, filepath):
        """
        Writes the results in PIPESTRESS .th format (ASCII columns).
        """
        # Ensure parent directory exists
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        
        segment_names = sorted(list(self.results.keys()))
        
        with open(filepath, "w") as f:
            # Header
            f.write("# TRACE Piping Force Time History File\n")
            f.write(f"# Units: {self.units}\n")
            f.write("# Columns:\n")
            f.write("#   1: Time (s)\n")
            for idx, name in enumerate(segment_names):
                f.write(f"#   {idx + 2}: Segment '{name}' ({self.units})\n")
            f.write("#\n")
            
            # Column headers
            headers = ["Time(s)"] + segment_names
            f.write(" ".join(f"{h:>20}" for h in headers) + "\n")
            
            # Data rows
            for t_idx, t in enumerate(self.times):
                row_vals = [t] + [self.results[name][t_idx] for name in segment_names]
                # Format to scientific notation with high precision
                f.write(" ".join(f"{val:>20.8e}" for val in row_vals) + "\n")

    def write_frc_file(self, filepath):
        """
        Writes the results in CAESAR II .frc format.
        Similar to .th, but using comma-separated values for easy spreadsheet/CAESAR II importing.
        """
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        
        segment_names = sorted(list(self.results.keys()))
        
        with open(filepath, "w") as f:
            f.write("Time(s)," + ",".join(segment_names) + "\n")
            for t_idx, t in enumerate(self.times):
                row_vals = [t] + [self.results[name][t_idx] for name in segment_names]
                f.write(",".join(f"{val:.8e}" for val in row_vals) + "\n")
