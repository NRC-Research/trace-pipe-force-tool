"""Configuration validation tests.

These cover the input checks that stand between a mistyped YAML file and a force
history that looks correct but is not. Each case here corresponds to a defect that
previously completed with exit status 0 and wrote a plausible output file.

Written against the standard library's unittest so they run with no additional
dependencies:

    python3 -m unittest discover -s tests

They are ordinary TestCase classes, so pytest collects them unchanged if it is
available.
"""

import os
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trace_force.config import AppConfig, ConfigurationError  # noqa: E402


def base_config():
    """A minimal configuration that must load cleanly."""
    return {
        "settings": {
            "ambient_pressure_pa": 101325.0,
            "units": "METRIC",
            "output_format": "TH",
        },
        "segments": [
            {
                "name": "Test_Segment",
                "direction_vector": [1.0, 0.0, 0.0],
                "components": [
                    {"id": 20, "type": "pipe", "cells": [1, 2], "cell_length": 0.5}
                ],
                "inlet_junction": {"type": "BOUNDED"},
                "outlet_junction": {"type": "BOUNDED"},
            }
        ],
    }


class ConfigCase(unittest.TestCase):
    def load(self, config):
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        yaml.safe_dump(config, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return AppConfig(handle.name)

    def assertRejected(self, config, *expected_fragments):
        with self.assertRaises(ConfigurationError) as caught:
            self.load(config)
        message = str(caught.exception)
        for fragment in expected_fragments:
            self.assertIn(fragment, message)
        return message


class BaseConfigLoads(ConfigCase):
    def test_minimal_config_is_accepted(self):
        config = self.load(base_config())
        self.assertEqual(len(config.segments), 1)
        self.assertEqual(config.segments[0].name, "Test_Segment")


class SegmentNames(ConfigCase):
    """Names become column headings in whitespace- and comma-delimited files."""

    def test_alphanumeric_underscore_and_hyphen_accepted(self):
        for name in ("A", "_leading", "VAL_002_Acoustic_Wave", "Loop-A", "seg1"):
            with self.subTest(name=name):
                config = base_config()
                config["segments"][0]["name"] = name
                self.assertEqual(self.load(config).segments[0].name, name)

    def test_comma_rejected_because_it_adds_a_frc_header_field(self):
        config = base_config()
        config["segments"][0]["name"] = "Riser,Downcomer"
        self.assertRejected(config, "not usable as an output column heading")

    def test_space_rejected_because_th_columns_are_whitespace_delimited(self):
        config = base_config()
        config["segments"][0]["name"] = "Loop A"
        self.assertRejected(config, "not usable as an output column heading")

    def test_newline_rejected(self):
        config = base_config()
        config["segments"][0]["name"] = "Riser\nInjected"
        self.assertRejected(config, "not usable as an output column heading")

    def test_formula_leading_characters_rejected(self):
        for name in ("=1+1", "+SUM(A1)", "-2+3", "@import"):
            with self.subTest(name=name):
                config = base_config()
                config["segments"][0]["name"] = name
                self.assertRejected(config, "not usable as an output column heading")

    def test_missing_name_rejected(self):
        config = base_config()
        del config["segments"][0]["name"]
        self.assertRejected(config, "must have a name")


class DuplicateSegmentNames(ConfigCase):
    """Results are keyed on name, so a duplicate silently drops a segment."""

    def test_duplicate_names_rejected_naming_both_indices(self):
        config = base_config()
        second = dict(config["segments"][0])
        config["segments"] = [config["segments"][0], second]
        message = self.assertRejected(config, "Duplicate segment name")
        self.assertIn("index 0", message)
        self.assertIn("index 1", message)

    def test_names_differing_by_case_are_distinct_columns(self):
        config = base_config()
        second = dict(config["segments"][0])
        second["name"] = "test_segment"
        config["segments"] = [config["segments"][0], second]
        self.assertEqual(len(self.load(config).segments), 2)


class CellLength(ConfigCase):
    """Length cannot come from the file: vol is time-independent."""

    def test_required(self):
        config = base_config()
        del config["segments"][0]["components"][0]["cell_length"]
        self.assertRejected(config, "must specify 'cell_length'")

    def test_zero_rejected(self):
        config = base_config()
        config["segments"][0]["components"][0]["cell_length"] = 0
        self.assertRejected(config, "finite length greater than zero")

    def test_negative_rejected(self):
        config = base_config()
        config["segments"][0]["components"][0]["cell_length"] = -0.5
        self.assertRejected(config, "finite length greater than zero")

    def test_non_numeric_rejected(self):
        config = base_config()
        config["segments"][0]["components"][0]["cell_length"] = "half a metre"
        self.assertRejected(config, "non-numeric")

    def test_positive_value_is_coerced_to_float(self):
        config = base_config()
        config["segments"][0]["components"][0]["cell_length"] = 2
        loaded = self.load(config)
        self.assertIsInstance(loaded.segments[0].components[0]["cell_length"], float)


class DirectionVector(ConfigCase):
    """Magnitude scales the gravity projection directly."""

    def test_unit_vectors_accepted(self):
        for vector in ([1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.6, 0.0, 0.8]):
            with self.subTest(vector=vector):
                config = base_config()
                config["segments"][0]["direction_vector"] = vector
                self.assertEqual(self.load(config).segments[0].direction_vector, vector)

    def test_non_unit_magnitude_rejected_and_normalised_form_suggested(self):
        config = base_config()
        config["segments"][0]["direction_vector"] = [0.0, 0.0, 5.2]
        message = self.assertRejected(config, "must be a unit vector")
        self.assertIn("5.2", message)
        self.assertIn("[0, 0, 1]", message)

    def test_zero_vector_reported_separately(self):
        config = base_config()
        config["segments"][0]["direction_vector"] = [0.0, 0.0, 0.0]
        self.assertRejected(config, "zero vector")

    def test_wrong_length_rejected(self):
        config = base_config()
        config["segments"][0]["direction_vector"] = [1.0, 0.0]
        self.assertRejected(config, "3D direction_vector")


class JunctionArea(ConfigCase):
    """The override was accepted and never applied; it is now refused."""

    def test_area_override_rejected_rather_than_silently_ignored(self):
        config = base_config()
        config["segments"][0]["outlet_junction"]["area"] = 0.0123
        self.assertRejected(config, "junction area override is not")

    def test_junctions_without_an_area_are_unaffected(self):
        loaded = self.load(base_config())
        self.assertEqual(loaded.segments[0].outlet_junction["type"], "BOUNDED")
        self.assertNotIn("area", loaded.segments[0].outlet_junction)

    def test_unknown_junction_type_rejected(self):
        config = base_config()
        config["segments"][0]["inlet_junction"]["type"] = "SEALED"
        self.assertRejected(config, "must be CONTINUED, BOUNDED, or OPEN")


class LoaderBounds(ConfigCase):
    """The YAML load itself must be bounded.

    segments.yaml is received from other teams, and every validation above runs
    only after yaml has already built the document. PyYAML flattens merge keys
    ('<<') by concatenating the merged pair lists without limit, so a
    sub-kilobyte document of nested '<<: [*a, *a, ...]' merges allocates
    multiplicatively inside the load - a file size cap alone cannot stop it.
    """

    def load_text(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return AppConfig(handle.name)

    def assertTextRejected(self, text, *expected_fragments):
        with self.assertRaises(ConfigurationError) as caught:
            self.load_text(text)
        message = str(caught.exception)
        for fragment in expected_fragments:
            self.assertIn(fragment, message)

    def test_merge_key_bomb_rejected_during_load(self):
        lines = ["l0: &l0 {k: 1}"]
        for d in range(1, 9):
            refs = ", ".join(["*l%d" % (d - 1)] * 9)
            lines.append("l%d: &l%d {<<: [%s]}" % (d, d, refs))
        bomb = "\n".join(lines)
        self.assertLess(len(bomb), 1024)  # well under the size cap by design
        self.assertTextRejected(bomb, "mapping expansion exceeds")

    def test_oversized_file_rejected(self):
        config = base_config()
        padding = "# " + "x" * (1024 * 1024) + "\n"
        self.assertTextRejected(
            padding + yaml.safe_dump(config), "Configuration file too large"
        )

    def test_ordinary_merge_keys_still_resolve(self):
        # The bound must not break safe_load semantics for sane documents.
        text = (
            "settings: {units: METRIC, output_format: TH}\n"
            "pipe_defaults: &pipe {type: pipe, cell_length: 0.5}\n"
            "segments:\n"
            "  - name: Test_Segment\n"
            "    direction_vector: [1.0, 0.0, 0.0]\n"
            "    components:\n"
            "      - {<<: *pipe, id: 20, cells: [1, 2]}\n"
            "    inlet_junction: {type: BOUNDED}\n"
            "    outlet_junction: {type: BOUNDED}\n"
        )
        loaded = self.load_text(text)
        self.assertEqual(loaded.segments[0].components[0]["cell_length"], 0.5)

    def test_repository_configs_still_load(self):
        # Named explicitly rather than globbed, so an in-progress scratch
        # config sitting untracked in the directory cannot fail the suite.
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in (
            "segments_VAL_001.yaml",
            "segments_VAL_001_friction_only.yaml",
            "segments_VAL_002.yaml",
            "segments_VAL_003.yaml",
            "segments_VAL_004.yaml",
        ):
            with self.subTest(name=name):
                AppConfig(os.path.join(repo, "test-validation", name))


if __name__ == "__main__":
    unittest.main()
