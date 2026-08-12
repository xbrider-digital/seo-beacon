#!/usr/bin/env bash
# Install SEO Beacon as a personal Claude Code skill (available in every project).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/seo-beacon"

command -v python3 >/dev/null 2>&1 || { echo "error: python3 not found on PATH"; exit 1; }

mkdir -p "$DEST"
cp "$SRC/SKILL.md" "$SRC/seo_beacon.py" "$DEST/"
chmod +x "$DEST/seo_beacon.py"

echo "Installed to $DEST"
echo
echo "Restart Claude Code, then run:  /seo-beacon"
echo "It will ask which website to audit on first use."
echo
echo "Or use it directly:  python3 $DEST/seo_beacon.py https://your-site.com/ --save-site"
