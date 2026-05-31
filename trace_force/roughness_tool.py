import math
import sys
import argparse

def calculate_equivalent_roughness(k_factor, nominal_roughness, vol_length, vol_dia):
    """
    Computes equivalent roughness epsilon_e for a single volume cell adjacent to an elbow.
    Takes:
      - k_factor: Form loss K for this volume (usually K_elbow / 2)
      - nominal_roughness: Pipe wall roughness epsilon (m or ft)
      - vol_length: Volume cell length L (m or ft)
      - vol_dia: Volume cell diameter D (m or ft)
    """
    # 1. Turbulent friction factor ft from Colebrook equation
    rel_roughness = nominal_roughness / (3.7 * vol_dia)
    ft = 1.0 / (-2.0 * math.log10(rel_roughness))**2
    
    # 2. Equivalent L/D ratio of form loss: (L/D)_L = K / f_t
    ld_loss = k_factor / ft
    
    # 3. Volume L/D ratio: (L/D)_v = L / D
    ld_vol = vol_length / vol_dia
    
    # 4. Equivalent turbulent friction factor f_e
    fe = ft * (1.0 + (ld_loss / ld_vol))
    
    # 5. Equivalent roughness epsilon_e
    epsilon_e = 3.7 * vol_dia * (10**(-1.0 / (2.0 * math.sqrt(fe))))
    
    return ft, ld_loss, ld_vol, fe, epsilon_e

def main():
    parser = argparse.ArgumentParser(
        description="Calculate TRACE equivalent volume roughness (epsilon_e) for elbow form losses"
    )
    parser.add_argument("-k", "--k-factor", type=float, required=True, help="Total elbow loss coefficient K")
    parser.add_argument("-r", "--roughness", type=float, required=True, help="Nominal pipe wall roughness (e.g. 0.00015 ft or 4.5e-5 m)")
    parser.add_argument("-l", "--length", type=float, required=True, help="Length of the adjacent volume cell")
    parser.add_argument("-d", "--diameter", type=float, required=True, help="Diameter of the adjacent volume cell")

    args = parser.parse_args()

    # In R5FORCE, half of the elbow resistance is added to the upstream volume, half downstream.
    k_half = args.k_factor / 2.0
    
    ft, ld_loss, ld_vol, fe, epsilon_e = calculate_equivalent_roughness(
        k_half, args.roughness, args.length, args.diameter
    )
    
    print("=" * 60)
    print(" Watkins Equivalent Roughness Calculation for Elbows")
    print("=" * 60)
    print(f"Inputs:")
    print(f"  Elbow total K-factor       : {args.k_factor:.4f}")
    print(f"  Assigned K-factor (K/2)    : {k_half:.4f}")
    print(f"  Nominal pipe roughness     : {args.roughness:.6e}")
    print(f"  Volume cell length         : {args.length:.4f}")
    print(f"  Volume cell diameter       : {args.diameter:.4f}")
    print("-" * 60)
    print(f"Calculated Parameters:")
    print(f"  Turbulent friction factor  (f_t) : {ft:.6f}")
    print(f"  Loss equivalent L/D ratio  (L/D)_L: {ld_loss:.4f}")
    print(f"  Volume L/D ratio           (L/D)_v: {ld_vol:.4f}")
    print(f"  Equivalent friction factor (f_e) : {fe:.6f}")
    print("-" * 60)
    print(f"Result:")
    print(f"  Equivalent Volume Roughness (epsilon_e): {epsilon_e:.6e}")
    print("=" * 60)

if __name__ == "__main__":
    main()
