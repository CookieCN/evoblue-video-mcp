#!/bin/bash
set -e

required_files=(
  "AGENTS.md"
  ".agents/feature_list.json"
  ".agents/progress.md"
  ".agents/CHANGELOG.md"
  ".agents/experience.md"
)

echo "EvoBlue Video MCP — Harness check"

test -d ".agents" || { echo "ERROR: missing .agents/" >&2; exit 1; }
for file in "${required_files[@]}"; do
  test -f "$file" || { echo "ERROR: missing $file" >&2; exit 1; }
  echo "OK: $file"
done

check_tool() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "OK: $label"
  else
    echo "MISSING: $label (environment prerequisite; nothing was installed)"
  fi
}

check_tool "Python 3.11+" python --version
check_tool "uv" uv --version
check_tool "Node.js" node --version
check_tool "npm" npm --version
check_tool "Git" git --version

echo
echo "Start work:"
echo "  1. Read AGENTS.md."
echo "  2. Read .agents/progress.md and relevant feature/contracts."
echo "  3. Make the smallest contract-aligned change."
echo "  4. Run tests, lint, type checks, and build as applicable."
echo
echo "Finish work:"
echo "  1. Update .agents/feature_list.json and .agents/progress.md."
echo "  2. Record reproducible lessons in .agents/experience.md."
echo "  3. Update .agents/CHANGELOG.md for meaningful changes."
echo "  4. Update AGENTS.md only when rules or architecture change."

