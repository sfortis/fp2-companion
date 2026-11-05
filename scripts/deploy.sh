#!/usr/bin/env bash
# Deploy the fp2_companion integration to a Home Assistant OS host over SSH.
#
#   scripts/deploy.sh
#
# The card is its own repository (sfortis/fp2-companion-card) and is
# installed and updated through the Marketplace, so this script deploys only
# the integration.
#
# It assumes SSH access to the host with Home Assistant's config folder at
# /config, as the "Advanced SSH & Web Terminal" app gives it. Settings come
# from the environment, or from scripts/deploy.env, which is not part of the
# repository:
#
#   SSH_HOST     the host as ssh knows it            (default: homeassistant)
#   CONFIG_DIR   Home Assistant's config folder      (default: /config)
#   RESTART      1 restarts Home Assistant after the copy (default: 0)
#
# Only files tracked by git are deployed, every one is validated first, and a
# file that does not parse refuses the whole deploy. Home Assistant loads the
# new code only when it restarts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[ -f "$ROOT/scripts/deploy.env" ] && . "$ROOT/scripts/deploy.env"
SSH_HOST="${SSH_HOST:-homeassistant}"
CONFIG_DIR="${CONFIG_DIR:-/config}"
RESTART="${RESTART:-0}"
PACKAGE="custom_components/fp2_companion"

cd "$ROOT"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: $ROOT is not a git work tree, so there is no file list to deploy." >&2
  exit 1
fi

deploy_integration() {
  local files=() f failed=0
  while IFS= read -r -d '' f; do files+=("$f"); done \
    < <(git ls-files -z -- "$PACKAGE")
  if [ ${#files[@]} -eq 0 ]; then
    echo "ERROR: nothing under $PACKAGE is tracked by git." >&2
    exit 1
  fi
  echo "Validating..."
  for f in "${files[@]}"; do
    [ -f "$f" ] || { echo "ERROR: $f is tracked but missing." >&2; exit 1; }
    case "$f" in
      *.py)
        python3 -c "import ast, sys; ast.parse(open(sys.argv[1]).read())" "$f" \
          || { echo "  $f FAILED"; failed=$((failed + 1)); } ;;
      *.json)
        python3 -c "import json, sys; json.load(open(sys.argv[1]))" "$f" \
          || { echo "  $f FAILED"; failed=$((failed + 1)); } ;;
    esac
  done
  if [ "$failed" -ne 0 ]; then
    echo "Refusing to deploy: $failed file(s) did not validate." >&2
    exit 1
  fi

  local tarball remote="/tmp/_fp2_companion.tgz"
  tarball="$(mktemp)"
  # The files keep their custom_components/ prefix, so they land in
  # $CONFIG_DIR/custom_components/fp2_companion. Files deleted from the repo
  # are deleted on the host too, because the folder is replaced as a whole.
  printf '%s\0' "${files[@]}" | tar czf "$tarball" --null -T -
  scp -q "$tarball" "$SSH_HOST:$remote"
  # Extract next to the integration and swap, so a failed tar leaves the
  # installed integration as it was.
  ssh "$SSH_HOST" "set -e; staging=\$(mktemp -d '$CONFIG_DIR/.fp2_companion_deploy.XXXXXX'); \
    trap 'rm -rf \"\$staging\"; rm -f $remote' EXIT; \
    tar xzf $remote -C \"\$staging\"; \
    rm -rf '$CONFIG_DIR/$PACKAGE.old'; \
    if [ -d '$CONFIG_DIR/$PACKAGE' ]; then mv '$CONFIG_DIR/$PACKAGE' '$CONFIG_DIR/$PACKAGE.old'; fi; \
    mv \"\$staging/$PACKAGE\" '$CONFIG_DIR/$PACKAGE'; \
    rm -rf '$CONFIG_DIR/$PACKAGE.old'"
  rm -f "$tarball"
  echo "Integration: ${#files[@]} file(s) to $SSH_HOST:$CONFIG_DIR/$PACKAGE"

  if [ "$RESTART" = "1" ]; then
    ssh "$SSH_HOST" "ha core restart"
    echo "Home Assistant is restarting."
  else
    echo "Restart Home Assistant to load it (or run with RESTART=1)."
  fi
}

deploy_integration
