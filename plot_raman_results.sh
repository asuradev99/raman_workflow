#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# plot_raman_results.sh  —  Plot Raman spectra + phonon band structure
#
# Usage:
#   bash /path/to/raman_workflow/plot_raman_results.sh <material_dir>
#
# Example:
#   bash ~/raman_workflow/plot_raman_results.sh ~/vasp_calculations/hBN
#
# This script:
#   1. Reads Raman_intensity_complex_*.eV files from the material's raman/ dir
#   2. Creates energy-specific subdirectories with properly formatted
#      Raman_intensity_specific.dat files (adds header row)
#   3. Runs SpectroPy's generate_raman_plots.py to create publication-quality
#      Lorentzian-broadened Raman spectra
#   4. Generates phonon band structure PDF via phonopy-bandplot
#   5. Generates 2D eigenvector (mode arrow) plots for each phonon mode
#
# Dependencies: conda environment with phonopy, matplotlib, numpy, pyyaml
#                (/global/common/software/m526/phonopy_env)
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <material_dir>"
    echo ""
    echo "Examples:"
    echo "  bash $0 /global/homes/e/easuresh/vasp_calculations/hBN"
    echo "  bash $0 \$RAMAN_PROJECT_DIR/MoS2"
    exit 1
fi

MATERIAL_DIR="$1"
RAMAN_DIR="${MATERIAL_DIR}/raman"

# ── Read display label from per-material workflow_settings.yaml ──────────────
# Use the "material" field (human-readable name) if available, else fall back to directory basename.
MATERIAL_LABEL="$(basename "$MATERIAL_DIR")"
_CONFIG_FILE="${MATERIAL_DIR}/workflow_settings.yaml"
if [ -f "$_CONFIG_FILE" ]; then
    _PARSED="$(grep -oP '^material:\s*\K.+' "$_CONFIG_FILE" 2>/dev/null || true)"
    [ -n "$_PARSED" ] && MATERIAL_LABEL="$_PARSED"
fi

# ── Validate ─────────────────────────────────────────────────────────────────
if [ ! -d "$RAMAN_DIR" ]; then
    echo "Error: RAMAN_DIR not found at '$RAMAN_DIR'"
    echo "Run the automation pipeline first to generate Raman data."
    exit 1
fi

# Find the Raman_intensity_complex_*.eV files, excluding broadened versions
ALL_FILES=($(ls "$RAMAN_DIR"/Raman_intensity_complex_*eV 2>/dev/null || true))
FILES=()
for F in "${ALL_FILES[@]}"; do
    BASENAME=$(basename "$F")
    # Skip broadened/renamed files that contain "broadening" or "polarization"
    if [[ "$BASENAME" != *"broadening"* && "$BASENAME" != *"polarization"* ]]; then
        FILES+=("$F")
    fi
done

if [ ${#FILES[@]} -eq 0 ]; then
    echo "Error: No Raman_intensity_complex_*.eV files found in $RAMAN_DIR"
    echo "Run the automation pipeline first to generate Raman data."
    exit 1
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Raman Spectrum Plotter"
echo "  Material: ${MATERIAL_LABEL}  ($(basename ${MATERIAL_DIR}))"
echo "  Energies found: ${#FILES[@]}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Determine script directory (where plot_raman_results.sh lives)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# SpectroPy is one level up from raman_workflow/
SPECTROPY_DIR="$(cd "$SCRIPT_DIR/../SpectroPy" && pwd)"
GENERATE_PLOTS="${SPECTROPY_DIR}/generate_raman_plots.py"

if [ ! -f "$GENERATE_PLOTS" ]; then
    echo "Error: generate_raman_plots.py not found at $GENERATE_PLOTS"
    exit 1
fi

# ── Clean up stray broadening_* directories from previous runs ───────────────
# These confuse SpectroPy's os.walk() because they contain 2-column data
# (no mode column) instead of the expected 3-column format.
cd "$RAMAN_DIR"
for DIR in broadening_*/; do
    [ -d "$DIR" ] || break  # skip if no match
    echo "  Removing stale directory: $(basename "$DIR")"
    rm -rf "$DIR"
done

# ── Prepare data files ───────────────────────────────────────────────────────

for FILE in "${FILES[@]}"; do
    BASENAME=$(basename "$FILE")                        # Raman_intensity_complex_1.96eV
    ENERGY="${BASENAME#Raman_intensity_complex_}"       # 1.96eV
    ENERGY_DIR="${ENERGY%eV}"                           # 1.96  (trim "eV" suffix)
    ENERGY_DIR="${ENERGY_DIR}eV"                        # 1.96eV (restore for dir name)
    
    echo ""
    echo "  Preparing: ${ENERGY}"
    
    # Create energy subdirectory
    mkdir -p "${RAMAN_DIR}/${ENERGY_DIR}"
    
    # Write header + data to Raman_intensity_specific.dat
    {
        echo "# Freq(cm-1)   Intensity(arb.)   Irrep."
        cat "$FILE"
    } > "${RAMAN_DIR}/${ENERGY_DIR}/Raman_intensity_specific.dat"
    
    echo "    -> ${ENERGY_DIR}/Raman_intensity_specific.dat"
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Running SpectroPy plotter..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Activate conda, load LaTeX, and run plotter ──────────────────────────────
source /global/common/software/m3035/conda/etc/profile.d/conda.sh
conda activate /global/common/software/m526/phonopy_env

# Load texlive module for matplotlib LaTeX rendering (usetex=True)
module load texlive/2024

# Run the plotter from the raman directory so it walks into energy subdirs
cd "$RAMAN_DIR"
# Pipe default values (FWHM=5.0, Lorentzian) to SpectroPy's input() prompts
echo -e "5.0\nl" | python3 "$GENERATE_PLOTS"

# ── 4. Phonon Band Structure (with mode legend) ─────────────────────────────
PLOT_BAND="${SCRIPT_DIR}/plot_band_structure.py"
HF_DIR="${MATERIAL_DIR}/hf"
BAND_YAML="${HF_DIR}/band.yaml"
IRREPS_YAML="${HF_DIR}/irreps.yaml"
BAND_PDF="${HF_DIR}/band_structure.pdf"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Phonon Band Structure (mode-coloured, with legend)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -f "$BAND_YAML" ] && [ -f "$IRREPS_YAML" ]; then
    python3 "$PLOT_BAND" \
        --band-yaml "$BAND_YAML" \
        --irreps   "$IRREPS_YAML" \
        --output   "$BAND_PDF"
elif [ -f "$BAND_YAML" ]; then
    # Fall back to phonopy-bandplot if irreps are missing
    cd "$HF_DIR"
    phonopy-bandplot band.yaml -o band_structure.pdf --legend
    echo "   -> Created ${BAND_PDF} (plain, no irrep legend)"
else
    echo "  Skipping: band.yaml not found in ${HF_DIR}"
fi

# ── 5. Phonon Mode Eigenvector Visualization ─────────────────────────────────
PLOT_EIGEN="${SCRIPT_DIR}/plot_phonon_modes.py"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Phonon Mode Eigenvector Plots..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -f "$BAND_YAML" ] && [ -f "${HF_DIR}/CONTCAR" ]; then
    cd "$HF_DIR"
    python3 "$PLOT_EIGEN" \
        --band-yaml "$BAND_YAML" \
        --contcar "${HF_DIR}/CONTCAR" \
        --output-dir "${HF_DIR}/mode_plots" \
        --irreps "${HF_DIR}/irreps.yaml"
    echo "  Done."
elif [ -f "${HF_DIR}/mode1" ]; then
    echo "  VMD scripts for all 6 modes already exist in ${HF_DIR}/"
    echo "  Open VMD and source them, or re-generate with:"
    echo "    cd ${HF_DIR} && python3 ${SPECTROPY_DIR}/visualize_modes.py"
else
    echo "  Skipping: CONTCAR or band.yaml not found in ${HF_DIR}"
fi

# ── 6. Electronic Density of States ──────────────────────────────────────────
PLOT_DOS="${SCRIPT_DIR}/plot_dos.py"
DOSCAR="${MATERIAL_DIR}/scf/DOSCAR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Electronic Density of States..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ -f "$DOSCAR" ]; then
    python3 "$PLOT_DOS" \
        --doscar "$DOSCAR" \
        --output "${HF_DIR}/dos.pdf"
else
    echo "  Skipping: DOSCAR not found in ${MATERIAL_DIR}/scf/"
fi

# ── 7. Aggregate to output/ ──────────────────────────────────────────────────
OUTPUT_DIR="${MATERIAL_DIR}/output"
RAMAN_OUT="${OUTPUT_DIR}/raman_spectra"
MODE_OUT="${OUTPUT_DIR}/phonon_modes"
DATA_OUT="${OUTPUT_DIR}/raman_data"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Aggregating results to output/ ..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

mkdir -p "$RAMAN_OUT" "$MODE_OUT" "$DATA_OUT"

# Raman spectra PNGs
for DIR in "$RAMAN_DIR"/*/; do
    SRC="${DIR}Raman_plot_styled.png"
    if [ -f "$SRC" ]; then
        ENERGY_NAME=$(basename "$DIR")
        cp "$SRC" "${RAMAN_OUT}/${ENERGY_NAME}.png"
        echo "    raman_spectra/${ENERGY_NAME}.png"
    fi
done

# Raman intensity data files
for F in "${FILES[@]}"; do
    cp "$F" "$DATA_OUT/"
    echo "    raman_data/$(basename "$F")"
done

# Broadened data files
for F in "$RAMAN_DIR"/Raman_intensity_complex_broadening_*.eV; do
    [ -f "$F" ] && cp "$F" "$DATA_OUT/" && echo "    raman_data/$(basename "$F")"
done

# Phonon band structure PDF
if [ -f "$BAND_PDF" ]; then
    cp "$BAND_PDF" "${OUTPUT_DIR}/phonon_band_structure.pdf"
    echo "    phonon_band_structure.pdf"
fi

# Composite phonon mode figure
MODE_FIG="${HF_DIR}/mode_plots/phonon_modes.png"
if [ -f "$MODE_FIG" ]; then
    cp "$MODE_FIG" "${MODE_OUT}/phonon_modes.png"
    echo "    phonon_modes/phonon_modes.png"
fi

# Mode summary table
if [ -f "${HF_DIR}/all_mode.txt" ]; then
    cp "${HF_DIR}/all_mode.txt" "${OUTPUT_DIR}/mode_summary.txt"
    echo "    mode_summary.txt"
fi

# Electronic density of states
DOS_PDF="${HF_DIR}/dos.pdf"
if [ -f "$DOS_PDF" ]; then
    cp "$DOS_PDF" "${OUTPUT_DIR}/dos.pdf"
    echo "    dos.pdf"
fi

# ── 8. Write README ──────────────────────────────────────────────────────────
cat > "${OUTPUT_DIR}/README.md" << EOFEOF
# ${MATERIAL_LABEL} — Raman & Phonon Results

Generated by \`plot_raman_results.sh\` on $(date '+%Y-%m-%d %H:%M').

## Contents

### \`raman_spectra/\`
Publication-style Raman spectra for each laser energy.
- Files: \`<energy>eV.png\` (e.g., \`1.96eV.png\`, \`2.33eV.png\`)
- Lorentzian broadening (FWHM = 5.0 cm⁻¹)

### \`raman_data/\`
Raw Raman intensity data from the automation pipeline.
- \`Raman_intensity_complex_<energy>eV\` — frequency, intensity, irrep (3 columns)
- \`Raman_intensity_complex_broadening_<energy>eV\` — broadened spectrum (2 columns)

### \`phonon_band_structure.pdf\`
Phonon dispersion along the high-symmetry path (Γ→M→K→Γ),
bands coloured by irreducible representation with mode-type legend.

### \`phonon_modes/\`
2D eigenvector (mode arrow) plots — all modes in a single composite figure.
- Top-down view of the unit cell with arrows showing atomic displacements
- File: \`phonon_modes.png\` (2×3 subplot grid)

### \`dos.pdf\`
Electronic density of states (total DOS), with Fermi level reference line.

### \`mode_summary.txt\`
Table of mode number, frequency (cm⁻¹), and irreducible representation.

## Workflow Status
EOFEOF

if [ -f "${MATERIAL_DIR}/workflow_status.txt" ]; then
    cat "${MATERIAL_DIR}/workflow_status.txt" >> "${OUTPUT_DIR}/README.md"
fi

echo ""
echo "   All results collected in: ${OUTPUT_DIR}/"

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Summary — ${MATERIAL_LABEL}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Raman spectra:     ${RAMAN_OUT}/"
for F in "$RAMAN_OUT"/*.png; do
    [ -f "$F" ] && echo "    $(basename "$F" .png)"
done
echo "  Phonon bands:      ${OUTPUT_DIR}/phonon_band_structure.pdf"
echo "  Mode plots:        ${MODE_OUT}/"
echo "  Electronic DOS:    ${OUTPUT_DIR}/dos.pdf"
echo "  Raw data:          ${DATA_OUT}/"
echo "  Mode summary:      ${OUTPUT_DIR}/mode_summary.txt"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
