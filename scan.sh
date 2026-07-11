#!/usr/bin/env bash

# Static-analysis / security scan for the custom component only.
#
# Runs bandit, semgrep, mypy and pip-audit. All file-scanning tools are
# scoped to the integration package under custom_components/wattpilot, and
# the vendored upstream `wattpilot` library bundled at
# custom_components/wattpilot/wattpilot/ is excluded so third-party code
# doesn't drown out findings in this repo's own code. See CLAUDE.md.
#
# Usage:
#   ./scan.sh                     # scan custom_components/wattpilot
#   ./scan.sh path/to/other/pkg   # scan a different target directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Target: default to the integration package, allow an override via $1.
TARGET="${1:-$SCRIPT_DIR/custom_components/wattpilot}"
if [ ! -d "$TARGET" ]; then
    echo "Error: target directory not found: $TARGET"
    exit 1
fi
echo "Scan target: $TARGET"

# Vendored upstream library to exclude from file-scanning tools. Note the
# integration package itself is named "wattpilot", so exclude patterns must
# target the *nested* wattpilot/wattpilot subtree specifically — a bare
# "wattpilot" pattern would match the target root and skip everything.
VENDORED_DIR="$TARGET/wattpilot"
VENDORED_REL="wattpilot/wattpilot/"
# semgrep matches --exclude against git-relative paths, so give it the vendored
# dir relative to the repo root (e.g. custom_components/wattpilot/wattpilot).
VENDORED_GIT_REL="$(realpath --relative-to="$SCRIPT_DIR" "$VENDORED_DIR")"

echo "=== 1. Setting up Virtual Environment ==="
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "=== 2. Ensuring Python Tools are Installed ==="
pip install --upgrade pip

TOOLS=(bandit semgrep mypy pip-audit)
for tool in "${TOOLS[@]}"; do
    if ! command -v "$tool" &> /dev/null; then
        echo "$tool not found. Installing..."
        pip install "$tool"
    else
        echo "$tool is already installed."
    fi
done

echo "=== 3. Running Scanners (scoped to $TARGET, excluding vendored lib) ==="

# 1. Bandit: recurse the target package for common security issues,
#    skipping the vendored upstream library.
echo "--> Running Bandit..."
bandit -r "$TARGET" -x "$VENDORED_DIR" || echo "Bandit found issues."

# 2. Semgrep: auto ruleset against the target package only, excluding the
#    vendored library subtree.
echo "--> Running Semgrep..."
semgrep scan --config auto --exclude "$VENDORED_GIT_REL" "$TARGET" || echo "Semgrep found issues."

# 3. Mypy: static type checking of the target package only, excluding the
#    vendored library subtree.
echo "--> Running Mypy..."
mypy "$TARGET" --exclude "$VENDORED_REL" || echo "Mypy found type errors."

# 4. Pip-audit: audit the integration's declared runtime dependencies
#    (manifest.json "requirements") for known-vulnerable packages. There is
#    no requirements_test.txt in this repo, so the requirement list is
#    extracted from the manifest into a temp file. pip-audit is
#    dependency- not path-scoped by nature.
echo "--> Running Pip-audit..."
REQ_FILE="$(mktemp)"
python3 -c "import json; print('\n'.join(json.load(open('$TARGET/manifest.json'))['requirements']))" > "$REQ_FILE"
pip-audit -r "$REQ_FILE" || echo "Pip-audit found vulnerable packages."
rm -f "$REQ_FILE"

echo "=== 4. Cleaning Up ==="
deactivate

echo "Done!"
