import math
import yaml
import os

class ConfigurationError(Exception):
    pass

class SegmentConfig:
    def __init__(self, data):
        self.name = data.get("name")
        if not self.name:
            raise ConfigurationError("Segment must have a name.")
        
        self.direction_vector = data.get("direction_vector")
        if not self.direction_vector or len(self.direction_vector) != 3:
            raise ConfigurationError(f"Segment '{self.name}' must have a 3D direction_vector [x, y, z].")
        try:
            self.direction_vector = [float(x) for x in self.direction_vector]
        except ValueError:
            raise ConfigurationError(f"Segment '{self.name}' direction_vector elements must be numbers.")

        self.components = data.get("components", [])
        if not self.components:
            raise ConfigurationError(f"Segment '{self.name}' must define at least one component.")
        
        for idx, comp in enumerate(self.components):
            if "id" not in comp:
                raise ConfigurationError(f"Component at index {idx} in segment '{self.name}' must have an 'id'.")
            if "type" not in comp:
                raise ConfigurationError(f"Component at index {idx} in segment '{self.name}' must have a 'type'.")
            if "cells" not in comp or not isinstance(comp["cells"], list) or len(comp["cells"]) == 0:
                raise ConfigurationError(f"Component at index {idx} in segment '{self.name}' must specify a non-empty list of 'cells'.")
            comp["id"] = int(comp["id"])
            comp["type"] = str(comp["type"]).lower()
            comp["cells"] = [int(c) for c in comp["cells"]]

            # cell_length is optional, but a zero or negative value silently removes
            # this component's shear and gravity contribution - the boundary terms
            # keep the result looking plausible - so reject it rather than compute
            # with it.  Only presence and type were checked before, which is what
            # let an honest typo through.
            if comp.get("cell_length") is not None:
                try:
                    cell_length = float(comp["cell_length"])
                except (TypeError, ValueError):
                    raise ConfigurationError(
                        f"Component at index {idx} in segment '{self.name}' has a non-numeric "
                        f"'cell_length' ({comp['cell_length']!r})."
                    )
                if not math.isfinite(cell_length) or cell_length <= 0.0:
                    raise ConfigurationError(
                        f"Component at index {idx} in segment '{self.name}' has 'cell_length' "
                        f"{cell_length!r}; it must be a finite length greater than zero. A "
                        f"non-positive length zeroes the shear and gravity terms for every cell "
                        f"in this component while the boundary terms keep the output plausible."
                    )
                comp["cell_length"] = cell_length

        # Parse inlet and outlet junction definitions
        self.inlet_junction = self._parse_junction(data.get("inlet_junction"), f"Segment '{self.name}' inlet_junction")
        self.outlet_junction = self._parse_junction(data.get("outlet_junction"), f"Segment '{self.name}' outlet_junction")

    def _parse_junction(self, data, context):
        if not data:
            return None
        j_type = str(data.get("type", "continued")).upper()
        if j_type not in ["CONTINUED", "BOUNDED", "OPEN"]:
            raise ConfigurationError(f"{context} type must be CONTINUED, BOUNDED, or OPEN.")
        
        j_id = data.get("id")
        if j_id is not None:
            j_id = int(j_id)
            
        return {
            "type": j_type,
            "id": j_id,
            "area": data.get("area") # optional override
        }

class AppConfig:
    def __init__(self, filepath):
        if not os.path.exists(filepath):
            raise ConfigurationError(f"Configuration file not found: {filepath}")
        
        with open(filepath, "r") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ConfigurationError(f"Error parsing YAML configuration: {e}")

        if not data:
            raise ConfigurationError("Configuration file is empty.")

        settings_data = data.get("settings", {})
        self.ambient_pressure_pa = float(settings_data.get("ambient_pressure_pa", 101325.0))
        self.units = str(settings_data.get("units", "METRIC")).upper()
        if self.units not in ["METRIC", "BRITISH"]:
            raise ConfigurationError("Settings 'units' must be METRIC or BRITISH.")
        
        self.output_format = str(settings_data.get("output_format", "TH")).upper()
        if self.output_format not in ["TH", "FRC"]:
            raise ConfigurationError("Settings 'output_format' must be TH or FRC.")

        segments_data = data.get("segments", [])
        if not segments_data:
            raise ConfigurationError("No piping segments defined under 'segments'.")

        self.segments = []
        for seg_data in segments_data:
            self.segments.append(SegmentConfig(seg_data))
