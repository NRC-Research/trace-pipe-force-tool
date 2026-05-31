#!/bin/bash
# Wrapper script to run TRACE locally via the Apptainer SIF container inside the Lima VM.
# Home directory paths map 1-to-1 between host and guest.

set -e

# Make sure the apptainer Lima VM is started
limactl start apptainer >/dev/null 2>&1

# Run the TRACE SIF container via Lima shell
limactl shell apptainer apptainer run /Users/cgg-mac/TRACE-pipe-force-tool/trace-V5.1831.1-linux_aarch64-gfortran.sif "$@"
