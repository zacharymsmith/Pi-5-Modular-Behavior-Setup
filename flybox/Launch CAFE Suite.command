#!/bin/bash
# CAFE Analysis Suite - macOS launcher (conda). Uses an isolated env "flybox-cafe"
# built from requirements.txt — nothing is installed into your base/system Python.
cd "$(dirname "$0")"
ENVNAME=flybox-cafe
echo "Starting CAFE Analysis Suite..."

# --- locate conda ---
CBASE=""
for p in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" "$HOME/mambaforge" \
         "/opt/miniconda3" "/opt/anaconda3" "/opt/homebrew/Caskroom/miniconda/base"; do
  [ -f "$p/etc/profile.d/conda.sh" ] && { CBASE="$p"; break; }
done
if [ -z "$CBASE" ] && command -v conda >/dev/null 2>&1; then
  CBASE="$(conda info --base 2>/dev/null)"
fi
if [ -z "$CBASE" ] || [ ! -f "$CBASE/etc/profile.d/conda.sh" ]; then
  echo "Could not find conda. Install Miniconda, or open a terminal where 'conda' works and run this file."
  read -n1 -rp "Press any key to close..."; exit 1
fi

# --- activate conda (works even though conda doesn't auto-activate) ---
source "$CBASE/etc/profile.d/conda.sh"

# --- create the env once from requirements.txt ---
if conda env list | awk '{print $1}' | grep -qx "$ENVNAME"; then
  conda activate "$ENVNAME"
else
  echo "Creating conda env '$ENVNAME' (one time, ~1-2 min)..."
  conda create -y -n "$ENVNAME" python=3.11 || { echo "env create failed"; read -n1 -r; exit 1; }
  conda activate "$ENVNAME"
  python -m pip install -r requirements-analysis.txt || { echo "dependency install failed"; read -n1 -r; exit 1; }
fi

echo "Launching in conda env '$ENVNAME'..."
python cafe_suite.py
