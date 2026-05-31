import math
from .config import SegmentConfig, AppConfig
from .xtv_extractor import XtvExtractor

class ForceEngine:
    def __init__(self, app_config: AppConfig, extractor: XtvExtractor, mock_friction: float = None):
        self.app_config = app_config
        self.extractor = extractor
        self.times = extractor.times
        self.num_steps = len(self.times)
        self.mock_friction = mock_friction
        
        # SI constants
        self.g_si = 9.80665  # m/s^2
        self.gravity_vector_si = [0.0, 0.0, -self.g_si]

    def run(self):
        """
        Computes the dynamic forces for all segments.
        Returns a dict of segment_name -> list of force values over time.
        """
        results = {}
        for segment in self.app_config.segments:
            results[segment.name] = self.compute_segment_forces(segment)
        return results

    def compute_segment_forces(self, segment: SegmentConfig):
        # 1. Initialize forces list for this segment
        segment_forces = [0.0] * self.num_steps

        # Check that wall friction factors 'wfl' and 'wfv' are present in XTV
        # unless mock_friction is specified.
        if self.mock_friction is None:
            first_comp = segment.components[0]["id"]
            if not self.extractor.has_variable(first_comp, "wfl") or not self.extractor.has_variable(first_comp, "wfv"):
                raise ValueError(
                    f"Required wall friction factors 'wfl' or 'wfv' are missing for component {first_comp} in the XTV file. "
                    "Option A requires running TRACE with graphLevel = 'full' in the Namelist, "
                    "or you can bypass this by passing a mock friction factor using --mock-friction."
                )

        # 2. Sum cell-centered forces (shear + gravity)
        for comp in segment.components:
            comp_id = comp["id"]
            cells = comp["cells"]

            for cell_idx in cells:
                # Extract time vectors for cell variables
                pn = self.extractor.get_cell_variable(comp_id, cell_idx, "pn")
                alpn = self.extractor.get_cell_variable(comp_id, cell_idx, "alpn")
                roln = self.extractor.get_cell_variable(comp_id, cell_idx, "roln")
                rovn = self.extractor.get_cell_variable(comp_id, cell_idx, "rovn")
                rom = self.extractor.get_cell_variable(comp_id, cell_idx, "rom")
                vol = self.extractor.get_cell_variable(comp_id, cell_idx, "vol")
                fa = self.extractor.get_cell_variable(comp_id, cell_idx, "fa")

                # Edge variables (velocities at junctions)
                # Cell k is bounded by edge k (inlet) and edge k+1 (outlet)
                vln_in = self.extractor.get_edge_variable(comp_id, cell_idx, "vln")
                vln_out = self.extractor.get_edge_variable(comp_id, cell_idx + 1, "vln")
                vvn_in = self.extractor.get_edge_variable(comp_id, cell_idx, "vvn")
                vvn_out = self.extractor.get_edge_variable(comp_id, cell_idx + 1, "vvn")

                # Friction factors (Option A or Mocked)
                if self.mock_friction is not None:
                    wfl = [self.mock_friction] * self.num_steps
                    wfv = [self.mock_friction] * self.num_steps
                else:
                    wfl = self.extractor.get_cell_variable(comp_id, cell_idx, "wfl")
                    wfv = self.extractor.get_cell_variable(comp_id, cell_idx, "wfv")

                # Calculate cell-level force at each time step
                for t_idx in range(self.num_steps):
                    # Average phasic velocities to cell centers
                    u_f = 0.5 * (vln_in[t_idx] + vln_out[t_idx])
                    u_g = 0.5 * (vvn_in[t_idx] + vvn_out[t_idx])

                    # Area & Length calculations
                    area = fa[t_idx]
                    
                    # Try to get cell length from configuration
                    length = comp.get("cell_length")
                    if length is not None:
                        length = float(length)
                    else:
                        # Fallback to XTV vol variable (may be invalid or zero in some runs)
                        volume_xtv = vol[t_idx]
                        length = volume_xtv / area if area > 0 else 0.0
                    
                    # Compute volume from area and length
                    volume = area * length

                    # Wetted perimeter P_w = sqrt(4 * pi * A)
                    perimeter = math.sqrt(4.0 * math.pi * area)

                    # Phasic Wall Shear Force
                    # F_shear = f_w * rho * u * |u| * area_w / 8
                    # Wetted area = P_w * L
                    f_shear_f = (wfl[t_idx] * roln[t_idx] * u_f * abs(u_f) / 8.0) * (1.0 - alpn[t_idx]) * perimeter * length
                    f_shear_g = (wfv[t_idx] * rovn[t_idx] * u_g * abs(u_g) / 8.0) * alpn[t_idx] * perimeter * length
                    f_shear = f_shear_f + f_shear_g

                    # Gravity component along the segment direction
                    # F_gravity = rho_m * V * (g . e_seg)
                    g_proj = sum(g * e for g, e in zip(self.gravity_vector_si, segment.direction_vector))
                    f_gravity = rom[t_idx] * volume * g_proj

                    # Total cell force (shear + gravity)
                    # Note: Watkins formulation shear and gravity act in the flow direction.
                    # We sum them along the segment direction.
                    segment_forces[t_idx] += f_shear + f_gravity

        # 3. Add inlet and outlet boundary junction forces
        # First component's first cell
        in_comp = segment.components[0]
        in_comp_id = in_comp["id"]
        in_cell_idx = in_comp["cells"][0]

        # Last component's last cell
        out_comp = segment.components[-1]
        out_comp_id = out_comp["id"]
        out_cell_idx = out_comp["cells"][-1]

        # Extract pressure and momentum flux terms for boundary cells
        pn_in = self.extractor.get_cell_variable(in_comp_id, in_cell_idx, "pn")
        alpn_in = self.extractor.get_cell_variable(in_comp_id, in_cell_idx, "alpn")
        roln_in = self.extractor.get_cell_variable(in_comp_id, in_cell_idx, "roln")
        rovn_in = self.extractor.get_cell_variable(in_comp_id, in_cell_idx, "rovn")
        vln_in = self.extractor.get_edge_variable(in_comp_id, in_cell_idx, "vln")
        vvn_in = self.extractor.get_edge_variable(in_comp_id, in_cell_idx, "vvn")
        fa_in = self.extractor.get_cell_variable(in_comp_id, in_cell_idx, "fa")

        pn_out = self.extractor.get_cell_variable(out_comp_id, out_cell_idx, "pn")
        alpn_out = self.extractor.get_cell_variable(out_comp_id, out_cell_idx, "alpn")
        roln_out = self.extractor.get_cell_variable(out_comp_id, out_cell_idx, "roln")
        rovn_out = self.extractor.get_cell_variable(out_comp_id, out_cell_idx, "rovn")
        vln_out = self.extractor.get_edge_variable(out_comp_id, out_cell_idx + 1, "vln")
        vvn_out = self.extractor.get_edge_variable(out_comp_id, out_cell_idx + 1, "vvn")
        fa_out = self.extractor.get_cell_variable(out_comp_id, out_cell_idx, "fa")

        # Inlet Junction Type
        in_type = segment.inlet_junction["type"] if segment.inlet_junction else "CONTINUED"
        out_type = segment.outlet_junction["type"] if segment.outlet_junction else "CONTINUED"

        for t_idx in range(self.num_steps):
            # Calculate Inlet Pressure + Momentum Flux (X_in = P_in + rho * u^2)
            u_in_f = vln_in[t_idx]
            u_in_g = vvn_in[t_idx]
            rho_u2_in = alpn_in[t_idx] * rovn_in[t_idx] * (u_in_g**2) + (1.0 - alpn_in[t_idx]) * roln_in[t_idx] * (u_in_f**2)
            x_in = pn_in[t_idx] + rho_u2_in

            # Calculate Outlet Pressure + Momentum Flux (X_out = P_out + rho * u^2)
            u_out_f = vln_out[t_idx]
            u_out_g = vvn_out[t_idx]
            rho_u2_out = alpn_out[t_idx] * rovn_out[t_idx] * (u_out_g**2) + (1.0 - alpn_out[t_idx]) * roln_out[t_idx] * (u_out_f**2)
            x_out = pn_out[t_idx] + rho_u2_out

            p_ambient = self.app_config.ambient_pressure_pa
            a_in = fa_in[t_idx]
            a_out = fa_out[t_idx]

            # 1. Inlet boundary force F_1
            if in_type == "BOUNDED":
                f_inlet = -(x_in - p_ambient) * a_in
            elif in_type == "CONTINUED":
                # For Continued, assume no area change as a baseline
                f_inlet = 0.0
            else:
                f_inlet = 0.0

            # 2. Outlet boundary force F_2
            if out_type == "BOUNDED":
                f_outlet = (x_out - p_ambient) * a_out
            elif out_type == "OPEN":
                # Open junction acts on (A_cell - A_j)
                # For a pipe exit, A_j = A_cell generally, so this goes to zero.
                # Jet reaction (thrust) is calculated separately if needed.
                f_outlet = (x_out - p_ambient) * a_out
            elif out_type == "CONTINUED":
                f_outlet = 0.0
            else:
                f_outlet = 0.0

            segment_forces[t_idx] += f_inlet + f_outlet

        # 4. Perform unit conversions if needed
        # Standard XTV outputs are SI (Newtons). If BRITISH is requested, convert Newtons to lbf.
        if self.app_config.units == "BRITISH":
            # 1 Newton = 0.224809 lbf
            segment_forces = [f * 0.224809 for f in segment_forces]

        return segment_forces
