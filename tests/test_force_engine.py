"""Boundary junction force tests.

These pin the boundary-force semantics of each junction type against hand
computation, on a deliberately degenerate scenario: one cell, zero velocity,
zero friction, horizontal segment. Shear, momentum and gravity are then all
exactly zero and the segment force is the boundary terms alone, so each
expectation is a two-factor product checkable by eye.

The XTV extractor is replaced by a fake returning constant channels; no TRACE
run or XTV file is involved.

Written against the standard library's unittest so they run with no additional
dependencies:

    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trace_force.config import AppConfig  # noqa: E402
from trace_force.force_engine import ForceEngine  # noqa: E402

N_STEPS = 3
PRESSURE = 201325.0  # Pa
AMBIENT = 101325.0   # Pa; overpressure is exactly 1e5 Pa
CELL_AREA = 0.01     # m^2


class FakeExtractor:
    """Constant-valued stand-in for XtvExtractor."""

    cell_values = {
        "pn": PRESSURE,
        "alpn": 0.0,    # single-phase liquid
        "roln": 1000.0,
        "rovn": 1.0,
        "rom": 1000.0,
        "fa": CELL_AREA,
    }

    def __init__(self):
        self.times = [0.0, 0.5, 1.0]

    def has_variable(self, comp_id, name):
        return True

    def get_cell_variable(self, comp_id, cell_idx, name):
        return [self.cell_values[name]] * N_STEPS

    def get_edge_variable(self, comp_id, edge_idx, name):
        return [0.0] * N_STEPS  # vln, vvn: fluid at rest


def engine_for(inlet, outlet):
    config = {
        "settings": {
            "ambient_pressure_pa": AMBIENT,
            "units": "METRIC",
            "output_format": "TH",
        },
        "segments": [
            {
                "name": "Boundary_Case",
                "direction_vector": [1.0, 0.0, 0.0],  # horizontal: no gravity
                "components": [
                    {"id": 20, "type": "pipe", "cells": [1], "cell_length": 1.0}
                ],
                "inlet_junction": inlet,
                "outlet_junction": outlet,
            }
        ],
    }
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False)
    yaml.safe_dump(config, handle)
    handle.close()
    try:
        app_config = AppConfig(handle.name)
    finally:
        os.unlink(handle.name)
    # mock_friction 0.0: the shear term exists but is exactly zero
    return ForceEngine(app_config, FakeExtractor(), mock_friction=0.0)


def segment_force(inlet, outlet):
    forces = engine_for(inlet, outlet).run()["Boundary_Case"]
    assert len(forces) == N_STEPS
    assert forces.count(forces[0]) == N_STEPS  # constant scenario
    return forces[0]


class BoundaryJunctionForces(unittest.TestCase):
    def test_bounded_outlet_full_end_thrust(self):
        force = segment_force({"type": "CONTINUED"}, {"type": "BOUNDED"})
        self.assertAlmostEqual(force, (PRESSURE - AMBIENT) * CELL_AREA)  # 1000 N

    def test_bounded_inlet_opposes_bounded_outlet(self):
        force = segment_force({"type": "BOUNDED"}, {"type": "BOUNDED"})
        self.assertAlmostEqual(force, 0.0)

    def test_open_outlet_without_area_is_a_plain_exit(self):
        # A_j defaults to the cell area: the lip is zero and so is the force.
        # Previously this computed the full closed-end thrust, identical to
        # BOUNDED (#8).
        force = segment_force({"type": "CONTINUED"}, {"type": "OPEN"})
        self.assertEqual(force, 0.0)

    def test_open_outlet_with_smaller_junction_acts_on_the_lip(self):
        # (P - P_amb) * (A_cell - A_j) = 1e5 * (0.01 - 0.004) = 600 N
        force = segment_force(
            {"type": "CONTINUED"}, {"type": "OPEN", "area": 0.004}
        )
        self.assertAlmostEqual(force, 600.0)

    def test_open_inlet_lip_force_has_opposite_sign(self):
        force = segment_force(
            {"type": "OPEN", "area": 0.004}, {"type": "CONTINUED"}
        )
        self.assertAlmostEqual(force, -600.0)

    def test_open_junction_area_equal_to_cell_area_is_zero(self):
        # An explicit A_j equal to the cell bore is the same plain exit.
        force = segment_force(
            {"type": "CONTINUED"}, {"type": "OPEN", "area": CELL_AREA}
        )
        self.assertAlmostEqual(force, 0.0)

    def test_continued_both_ends_no_boundary_force(self):
        force = segment_force({"type": "CONTINUED"}, {"type": "CONTINUED"})
        self.assertEqual(force, 0.0)


if __name__ == "__main__":
    unittest.main()
