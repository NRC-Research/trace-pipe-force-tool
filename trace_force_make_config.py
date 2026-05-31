#!/usr/bin/env python3
import sys
import os
import argparse
import re
import yaml

# Add current folder to path to import trace_force
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from trace_force import xtvreader

def main():
    parser = argparse.ArgumentParser(
        description="Helper utility to generate a template segments YAML file from a TRACE XTV output file."
    )
    parser.add_argument("-i", "--input", required=True, help="Path to the TRACE binary XTV file")
    parser.add_argument("-o", "--output", default="template_segments.yaml", help="Path to write the output skeleton YAML")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input XTV file not found: {args.input}")
        sys.exit(1)

    print(f"Opening XTV file: {args.input}...")
    try:
        with open(args.input, "rb") as f:
            xtv = xtvreader.XtvFile(f, verbose=False)
            
            pipes = []
            # Find all pipe components
            for (cid, ctype), comp in xtv.components.items():
                if ctype.lower() == "pipe":
                    # Determine cell count from the pressure ('pn') channel length
                    max_cell = 0
                    if "pn" in comp.channels:
                        max_cell = comp.channels["pn"].vLength
                    
                    pipes.append({
                        "id": cid,
                        "cells": max_cell
                    })
            
            if not pipes:
                print("No PIPE components found in the XTV file.")
                sys.exit(0)
                
            print(f"Found {len(pipes)} PIPE component(s). Generating template config...")
            
            # Construct standard YAML skeleton structure
            config_dict = {
                "settings": {
                    "ambient_pressure_pa": 101325.0,
                    "units": "METRIC",
                    "output_format": "TH"
                },
                "segments": []
            }
            
            for pipe in sorted(pipes, key=lambda x: x["id"]):
                cid = pipe["id"]
                n_cells = pipe["cells"]
                
                segment = {
                    "name": f"Pipe_{cid}_Segment",
                    "direction_vector": [1.0, 0.0, 0.0],
                    "components": [
                        {
                            "id": cid,
                            "type": "pipe",
                            "cells": list(range(1, n_cells + 1)),
                            "cell_length": 1.0
                        }
                    ],
                    "inlet_junction": {
                        "type": "BOUNDED",
                        "id": 0
                    },
                    "outlet_junction": {
                        "type": "BOUNDED",
                        "id": 0
                    }
                }
                config_dict["segments"].append(segment)
                
            # Write to output file
            with open(args.output, "w") as out_f:
                # Custom header comments
                out_f.write("# TRACE Force Calculation Segments Template Config\n")
                out_f.write(f"# Generated automatically from: {os.path.basename(args.input)}\n")
                out_f.write("# TODO: Review and update the 'direction_vector', 'cell_length', and boundary junction settings.\n\n")
                yaml.dump(config_dict, out_f, default_flow_style=False, sort_keys=False)
                
            print(f"Success! Template configuration written to: {args.output}")
            
    except Exception as e:
        print(f"Error reading XTV file or generating config: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
