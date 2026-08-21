import math
import re
import yaml
import os

class ConfigurationError(Exception):
    pass


# Largest configuration file the loader will accept.  The configurations in this
# repository are all under a kilobyte; 1 MB leaves generous headroom while
# bounding what the YAML loader is asked to parse.
_MAX_CONFIG_BYTES = 1024 * 1024

# Cap on total mapping entries seen while flattening merge keys ('<<').  PyYAML
# concatenates merged pair lists without limit, so a sub-kilobyte document of
# nested '<<: [*a, *a, ...]' merges expands multiplicatively inside safe_load,
# before any of the validation below ever runs.  The size cap alone cannot stop
# that, which is why this loader is load-bearing rather than belt-and-braces.
_MAX_MAPPING_ENTRIES = 100000


class _BoundedSafeLoader(yaml.SafeLoader):
    """safe_load semantics with a bound on merge-key expansion.

    ConstructorError is a yaml.YAMLError subclass, so tripping the bound is
    reported through the same ConfigurationError path as any other parse
    failure.
    """

    def __init__(self, stream):
        super().__init__(stream)
        self._mapping_entries = 0

    def flatten_mapping(self, node):
        super().flatten_mapping(node)
        self._mapping_entries += len(node.value)
        if self._mapping_entries > _MAX_MAPPING_ENTRIES:
            raise yaml.constructor.ConstructorError(
                None, None,
                "mapping expansion exceeds %d entries" % _MAX_MAPPING_ENTRIES,
                node.start_mark)


# Segment names become column headings in both output formats.  The .th columns are
# whitespace-delimited and the .frc header is comma-joined, so a name containing a
# space, comma or newline changes how many columns the file appears to carry and
# which segment each one describes - silently, since the writers join whatever they
# are given.  A name beginning =, +, - or @ is additionally read as a live formula
# when the .frc is opened in a spreadsheet, which is what that format exists for.
_SEGMENT_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")

class SegmentConfig:
    def __init__(self, data):
        self.name = data.get("name")
        if self.name is None or self.name == "":
            raise ConfigurationError("Segment must have a name.")
        self.name = str(self.name)
        if not _SEGMENT_NAME_RE.match(self.name):
            raise ConfigurationError(
                f"Segment name {self.name!r} is not usable as an output column heading. "
                f"Use letters, digits, underscores and hyphens, starting with a letter, "
                f"digit or underscore. A name containing a space, comma or newline "
                f"changes the column layout of the .th and .frc files without changing "
                f"the data, and one beginning =, +, - or @ is treated as a formula when "
                f"the .frc is opened in a spreadsheet."
            )
        
        self.direction_vector = data.get("direction_vector")
        if not self.direction_vector or len(self.direction_vector) != 3:
            raise ConfigurationError(f"Segment '{self.name}' must have a 3D direction_vector [x, y, z].")
        try:
            self.direction_vector = [float(x) for x in self.direction_vector]
        except (TypeError, ValueError):
            raise ConfigurationError(f"Segment '{self.name}' direction_vector elements must be numbers.")

        # direction_vector is used in exactly one place: as the axis the gravity
        # vector is projected onto.  Its magnitude therefore scales the gravity term
        # directly - the zero vector deletes it, [0, 0, 5.2] multiplies it by 5.2 -
        # and nothing downstream notices, because the projection stays internally
        # consistent and the pressure and momentum terms are unaffected.  Only
        # presence, length and float-ness were checked before, which is what let a
        # displacement-style vector through.
        if not all(math.isfinite(x) for x in self.direction_vector):
            raise ConfigurationError(
                f"Segment '{self.name}' direction_vector elements must be finite numbers."
            )
        norm = math.sqrt(sum(x * x for x in self.direction_vector))
        if norm == 0.0:
            raise ConfigurationError(
                f"Segment '{self.name}' direction_vector is the zero vector. It must be a unit "
                f"vector giving the segment's orientation; a zero vector removes the gravity "
                f"contribution from this segment entirely."
            )
        if abs(norm - 1.0) > 1e-6:
            unit = ", ".join(f"{x / norm:.6g}" for x in self.direction_vector)
            raise ConfigurationError(
                f"Segment '{self.name}' direction_vector {self.direction_vector} has magnitude "
                f"{norm:.6g}, but it must be a unit vector. The magnitude scales the gravity term "
                f"directly, so this would multiply that term by {norm:.6g}. If the orientation is "
                f"correct, use [{unit}]."
            )

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

            # cell_length is required.  The only alternative the tool ever had was to
            # derive it from the XTV vol channel, but vol is time-independent and so
            # carries no per-edit data record; the offset computed for it indexes into
            # a neighbouring channel.  There is no way to obtain a length from the file.
            # A zero or negative value is rejected for the same reason it must not be
            # computed with: it removes this component's shear and gravity contribution
            # while the boundary terms keep the result looking plausible.
            if comp.get("cell_length") is None:
                raise ConfigurationError(
                    f"Component at index {idx} in segment '{self.name}' must specify "
                    f"'cell_length', the length of each cell in metres. It cannot be "
                    f"derived from the XTV file: the vol channel is time-independent and "
                    f"has no data records to read."
                )
            else:
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

        # 'area' is the junction flow area A_j, and it is meaningful only for an
        # OPEN junction: the boundary force there acts on the annular lip
        # (A_cell - A_j) left where the junction's opening is smaller than the
        # cell bore.  Without it, A_j is taken equal to the cell area - the
        # plain open exit, whose lip is zero.  For BOUNDED the force acts on
        # the full closed end and for CONTINUED there is no end at all, so an
        # 'area' on those types would do nothing; it is rejected rather than
        # accepted and ignored.
        area = data.get("area")
        if area is not None:
            if j_type != "OPEN":
                raise ConfigurationError(
                    f"{context} sets 'area', but a {j_type} junction does not use it: "
                    f"the boundary force is computed from the XTV flow area of the "
                    f"boundary cell (BOUNDED) or is zero (CONTINUED). Remove it, or use "
                    f"type OPEN if the force on the annular lip around a smaller "
                    f"opening is what is intended."
                )
            try:
                area = float(area)
            except (TypeError, ValueError):
                raise ConfigurationError(
                    f"{context} has a non-numeric 'area' ({data.get('area')!r})."
                )
            if not math.isfinite(area) or area <= 0.0:
                raise ConfigurationError(
                    f"{context} 'area' is {area!r}; it must be a finite junction flow "
                    f"area greater than zero, in square metres. To model a fully open "
                    f"plain exit, omit 'area' instead - the junction area then equals "
                    f"the cell area and the lip force is zero."
                )

        junction = {
            "type": j_type,
            "id": j_id,
        }
        if area is not None:
            junction["area"] = area
        return junction

class AppConfig:
    def __init__(self, filepath):
        if not os.path.exists(filepath):
            raise ConfigurationError(f"Configuration file not found: {filepath}")
        
        # Read through the open handle with a hard cap, so the size actually
        # parsed is the size checked - a stat-then-read pair could be raced.
        with open(filepath, "r") as f:
            text = f.read(_MAX_CONFIG_BYTES + 1)
        if len(text) > _MAX_CONFIG_BYTES:
            raise ConfigurationError(
                f"Configuration file too large (limit {_MAX_CONFIG_BYTES} bytes): {filepath}"
            )

        try:
            data = yaml.load(text, Loader=_BoundedSafeLoader)
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

        # Results are collected into a dict keyed on segment name, and both output
        # writers emit one column per key.  Two segments sharing a name therefore
        # produce a single column holding whichever was computed last, while the CLI
        # still reports the configured segment count and writes the file - an entire
        # segment's dynamic loads can leave the stress input with no indication.
        # Names are compared exactly, matching the key semantics of the dict this
        # protects: names differing only in case or surrounding space are distinct
        # keys and so produce distinct columns.
        seen = {}
        for idx, segment in enumerate(self.segments):
            if segment.name in seen:
                raise ConfigurationError(
                    f"Duplicate segment name {segment.name!r}: the segments at index "
                    f"{seen[segment.name]} and index {idx} both use it. Segment names key "
                    f"the computed results and become the output column headings, so a "
                    f"duplicate emits one column and silently discards the other "
                    f"segment's forces."
                )
            seen[segment.name] = idx
