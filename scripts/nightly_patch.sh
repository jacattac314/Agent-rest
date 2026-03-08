#!/usr/bin/env bash
# =============================================================================
# nightly_patch.sh — Nightly maintenance patch runner
# Schedule: 1 AM Central (07:00 UTC, adjusts for DST automatically via TZ)
#
# What it does:
#   1. Fetches the latest list of starred repos for jacattac314
#   2. Clones or updates each repo
#   3. Runs the Agent-rest scanner + docstring injector on each
#   4. Generates unified diffs (patches)
#   5. Commits all patches to the Agent-rest branch
# =============================================================================

set -euo pipefail

AGENT_ROOT="/home/user/Agent-rest"
REPOS_DIR="/home/user/repos"
PATCHES_DIR="${AGENT_ROOT}/patches"
GITHUB_USER="jacattac314"
BRANCH="claude/run-and-test-hlj1A"
LOGFILE="${AGENT_ROOT}/logs/nightly_patch.log"
DOCSTRING_SCRIPT="${AGENT_ROOT}/scripts/add_docstrings.py"

mkdir -p "${REPOS_DIR}" "${PATCHES_DIR}" "$(dirname "${LOGFILE}")"

exec >> "${LOGFILE}" 2>&1
echo ""
echo "========================================================================"
echo "Nightly patch run: $(TZ='America/Chicago' date '+%Y-%m-%d %I:%M %p %Z')"
echo "========================================================================"

# ---------------------------------------------------------------------------
# 1. Fetch starred repos
# ---------------------------------------------------------------------------
echo "[1/5] Fetching starred repos for ${GITHUB_USER}..."
STARRED=$(curl -sf \
  "https://api.github.com/users/${GITHUB_USER}/starred?per_page=100" \
  | python3 -c "import sys,json; [print(r['full_name']) for r in json.load(sys.stdin)]" \
  | grep "^${GITHUB_USER}/" || true)

if [[ -z "${STARRED}" ]]; then
  echo "ERROR: Could not fetch starred repos. Check network. Aborting."
  exit 1
fi

echo "Repos found:"
echo "${STARRED}" | sed 's/^/  /'

# ---------------------------------------------------------------------------
# 2. Clone or pull each repo
# ---------------------------------------------------------------------------
echo ""
echo "[2/5] Cloning / updating repos..."
ACTIVE_REPOS=()
while IFS= read -r FULL_NAME; do
  NAME="${FULL_NAME#*/}"
  DEST="${REPOS_DIR}/${NAME}"
  if [[ -d "${DEST}/.git" ]]; then
    echo "  pull: ${NAME}"
    git -C "${DEST}" fetch --quiet origin HEAD
    git -C "${DEST}" reset --quiet --hard FETCH_HEAD
  else
    echo "  clone: ${NAME}"
    git clone --quiet --depth=1 "https://github.com/${FULL_NAME}.git" "${DEST}" || {
      echo "  WARNING: clone failed for ${FULL_NAME}, skipping."
      continue
    }
  fi
  # Skip empty repos
  FILE_COUNT=$(find "${DEST}" -not -path '*/.git/*' -type f | wc -l)
  if [[ "${FILE_COUNT}" -eq 0 ]]; then
    echo "  skip (empty): ${NAME}"
    continue
  fi
  ACTIVE_REPOS+=("${DEST}")
done <<< "${STARRED}"

# ---------------------------------------------------------------------------
# 3. Run scanner to identify doc gaps, then inject docstrings
# ---------------------------------------------------------------------------
echo ""
echo "[3/5] Injecting docstrings into Python files..."
cd "${AGENT_ROOT}"
python3 "${DOCSTRING_SCRIPT}" "${ACTIVE_REPOS[@]}"

# ---------------------------------------------------------------------------
# 4. Generate patches for each modified repo
# ---------------------------------------------------------------------------
echo ""
echo "[4/5] Generating patch files..."
CHANGED=()
for DEST in "${ACTIVE_REPOS[@]}"; do
  NAME=$(basename "${DEST}")
  PATCHFILE="${PATCHES_DIR}/${NAME}.patch"

  # Unstaged changes (modified tracked files)
  git -C "${DEST}" diff > "${PATCHFILE}"

  # Also capture untracked new files (e.g. README.md additions)
  NEW_FILES=$(git -C "${DEST}" ls-files --others --exclude-standard)
  if [[ -n "${NEW_FILES}" ]]; then
    while IFS= read -r F; do
      git -C "${DEST}" diff --no-index /dev/null "${DEST}/${F}" >> "${PATCHFILE}" 2>/dev/null || true
    done <<< "${NEW_FILES}"
  fi

  LINES=$(wc -l < "${PATCHFILE}")
  if [[ "${LINES}" -gt 0 ]]; then
    echo "  ${NAME}: ${LINES} lines"
    CHANGED+=("${NAME}")
  else
    rm -f "${PATCHFILE}"
  fi

  # Reset repo so next run starts clean
  git -C "${DEST}" checkout -- . 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# 5. Commit patches to Agent-rest
# ---------------------------------------------------------------------------
echo ""
echo "[5/5] Committing patches to branch ${BRANCH}..."
cd "${AGENT_ROOT}"
git checkout "${BRANCH}" --quiet 2>/dev/null || true

# Re-generate the README for patches/
python3 - <<PYEOF
from pathlib import Path
import datetime

patches_dir = Path("${PATCHES_DIR}")
today = datetime.date.today().isoformat()
files = sorted(patches_dir.glob("*.patch"))

lines = [
    "# Maintenance Patches",
    "",
    f"Last generated: {today}  (scheduled 1 AM Central / 07:00 UTC daily)",
    "",
    "## Apply a patch",
    "",
    "\`\`\`bash",
    "cd /path/to/repo",
    "git apply patches/<repo>.patch",
    'git commit -am "chore: maintenance agent auto-fixes"',
    "git push",
    "\`\`\`",
    "",
    "## Available patches",
    "",
    "| Patch file | Size |",
    "|---|---|",
]
for f in files:
    size = f.stat().st_size
    lines.append(f"| \`{f.name}\` | {size:,} bytes |")

lines.append("")
(patches_dir / "README.md").write_text("\n".join(lines))
PYEOF

if [[ "${#CHANGED[@]}" -eq 0 ]]; then
  echo "No patches generated — nothing to commit."
  exit 0
fi

git add "${PATCHES_DIR}/"
git diff --cached --quiet && { echo "Nothing new to commit."; exit 0; }

NAMES=$(IFS=', '; echo "${CHANGED[*]}")
git commit -m "chore(nightly): maintenance patches $(TZ='America/Chicago' date '+%Y-%m-%d') — ${NAMES}

Auto-generated by nightly_patch.sh (1 AM Central).
Repos patched: ${#CHANGED[@]}

https://claude.ai/code/session_014kXwTVVoPq25Me9FShPT5K"

git push -u origin "${BRANCH}"
echo "Done. Patches committed and pushed."
