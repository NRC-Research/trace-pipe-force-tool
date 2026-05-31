import os
import sys
from pathlib import Path

# Add SNAP python path
SNAP_PYTHON_PATH = "/Users/cgg-mac/run-snap500/python"
if SNAP_PYTHON_PATH not in sys.path:
    sys.path.insert(0, SNAP_PYTHON_PATH)

try:
    import snap.model_editor as model_editor
    import snap.codes.trace as trace
    print("Initialising SNAP/TRACE API...")
    model_editor.find_plugin("TRACE")
    print("SNAP JVM connection ready.")
except Exception as e:
    print(f"Error initializing SNAP: {e}")
    sys.exit(1)

test_validation_dir = Path(__file__).parent.resolve()
models = ["VAL_002", "VAL_003", "VAL_004"]

for model_name in models:
    inp_path = test_validation_dir / f"{model_name}.inp"
    med_path = test_validation_dir / f"{model_name}.med"
    
    if not inp_path.exists():
        print(f"Error: Input deck {inp_path} does not exist.")
        continue
        
    print(f"\nProcessing {model_name}...")
    try:
        # 1. Import ASCII deck
        print(f"  Importing ASCII deck from {inp_path.name}...")
        model = trace.import_ascii(str(inp_path))
        print("  Import successful.")
        
        # 2. Save as SNAP .med file
        print(f"  Saving model to {med_path.name}...")
        model.save(str(med_path))
        print("  Save successful.")
        
        # 3. Clean up model from session
        model.close()
        print(f"  Successfully built SNAP model: {med_path.name}")
        
    except Exception as e:
        print(f"  Error processing {model_name}: {e}")
        import traceback
        traceback.print_exc()

print("\nModel build process completed.")
