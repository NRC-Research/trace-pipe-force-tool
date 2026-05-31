import argparse
import sys
import os
import traceback
from .config import AppConfig, ConfigurationError
from .xtv_extractor import XtvExtractor
from .force_engine import ForceEngine
from .output_writer import OutputWriter

def main():
    parser = argparse.ArgumentParser(
        description="TRACE Dynamic Piping Force Post-Processor (Watkins Pressure-Shear formulation)"
    )
    parser.add_argument(
        "-i", "--input", required=True, help="Path to the TRACE binary XTV file"
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the YAML configuration file defining piping segments"
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Path to write the calculated output force file"
    )
    parser.add_argument(
        "--mock-friction", type=float, default=None, help="Bypass missing wfl/wfv check and use a constant mock value"
    )

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input XTV file not found at '{args.input}'", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.config):
        print(f"Error: Configuration YAML file not found at '{args.config}'", file=sys.stderr)
        sys.exit(1)

    try:
        print("1. Parsing YAML configuration...")
        config = AppConfig(args.config)
        print(f"   Loaded {len(config.segments)} piping segment(s).")
        print(f"   Units: {config.units}, Output Format: {config.output_format}, Ambient Pressure: {config.ambient_pressure_pa} Pa")

        print("2. Extracting time-series variables from XTV file...")
        with XtvExtractor(args.input) as extractor:
            print(f"   Successfully opened XTV file with {len(extractor.times)} time steps.")

            print("3. Executing Watkins dynamic force solver...")
            engine = ForceEngine(config, extractor, mock_friction=args.mock_friction)
            results = engine.run()
            
            print("4. Writing outputs...")
            writer = OutputWriter(config, extractor.times, results)
            
            if config.output_format == "TH":
                writer.write_th_file(args.output)
            else:
                writer.write_frc_file(args.output)
                
            print(f"   Success! Force time history written to: {args.output}")

    except ConfigurationError as e:
        print(f"Configuration Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("Runtime Error occurred:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
