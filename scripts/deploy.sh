#!/usr/bin/env bash
# Deploy fp2_companion to a Home Assistant OS host over SSH.
#
#   scripts/deploy.sh
#
# The card ships inside the app folder (apps/fp2_companion/card/), and the app
# installs it into Home Assistant's www folder and points the dashboard
# resource at it when it starts (card_install.py), so there is no separate
# card step.
#
# It assumes Home Assistant OS with the AppDaemon add-on and SSH access to the
# host (for example the "Advanced SSH & Web Terminal" add-on with protection
# mode off). Settings come from the environment, or from scripts/deploy.env,
# which is not part of the repository:
#
#   SSH_HOST     the host as ssh knows it            (default: homeassistant)
#   ADDON_SLUG   the AppDaemon add-on's slug         (default: a0d7b954_appdaemon)
#   STAGE        a writable folder on the host       (default: /root)
#
# Only files tracked by git are deployed, every one is validated first, and a
# file that does not parse refuses the whole deploy. AppDaemon reloads the app
# by itself, and the app then updates the card and its resource's ?v=.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[ -f "$ROOT/scripts/deploy.env" ] && . "$ROOT/scripts/deploy.env"
SSH_HOST="${SSH_HOST:-homeassistant}"
ADDON_SLUG="${ADDON_SLUG:-a0d7b954_appdaemon}"
STAGE="${STAGE:-/root}"

cd "$ROOT"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: $ROOT is not a git work tree, so there is no file list to deploy." >&2
  exit 1
fi

check_js() {
  node --check "$1" || return 1
  # A back quote inside the CSS template literal ends the stylesheet early.
  local css
  css="$(awk '/^const STYLE = `/{on=1; next} on && /^`;/{exit} on' "$1")"
  if printf '%s' "$css" | grep -q '`'; then
    echo "  a back quote inside the STYLE block of $1" >&2
    return 1
  fi
}

deploy_app() {
  # Home Assistant renamed the add-on containers from addon_ to app_ in
  # 2026.2, so the name is resolved rather than written down.
  local addon
  addon="$(ssh "$SSH_HOST" "docker ps --format '{{.Names}}' | grep -E '^(app|addon)_${ADDON_SLUG}\$' | head -1")"
  if [ -z "$addon" ]; then
    echo "ERROR: no running container (app|addon)_${ADDON_SLUG} on $SSH_HOST" >&2
    exit 1
  fi

  local files=() f failed=0
  while IFS= read -r -d '' f; do files+=("$f"); done \
    < <(git ls-files -z -- apps/fp2_companion)
  if [ ${#files[@]} -eq 0 ]; then
    echo "ERROR: nothing under apps/fp2_companion is tracked by git." >&2
    exit 1
  fi
  echo "Validating..."
  for f in "${files[@]}"; do
    [ -f "$f" ] || { echo "ERROR: $f is tracked but missing." >&2; exit 1; }
    case "$f" in
      *.py)
        python3 -c "import ast, sys; ast.parse(open(sys.argv[1]).read())" "$f" \
          || { echo "  $f FAILED"; failed=$((failed + 1)); } ;;
      *.js)
        check_js "$f" || { echo "  $f FAILED"; failed=$((failed + 1)); } ;;
      *.yaml)
        python3 -c "
import sys, yaml
yaml.SafeLoader.add_constructor('!secret', lambda loader, node: '<secret>')
yaml.safe_load(open(sys.argv[1]))" "$f" \
          || { echo "  $f FAILED"; failed=$((failed + 1)); } ;;
    esac
  done
  if [ "$failed" -ne 0 ]; then
    echo "Refusing to deploy: $failed file(s) did not validate." >&2
    exit 1
  fi

  local tarball
  tarball="$(mktemp)"
  # The files go in without the leading apps/, so they land in /config/apps.
  printf '%s\0' "${files[@]#apps/}" | tar czf "$tarball" -C apps --null -T -
  scp -q "$tarball" "$SSH_HOST:$STAGE/_fp2_companion.tgz"
  ssh "$SSH_HOST" "docker cp $STAGE/_fp2_companion.tgz $addon:/tmp/_fp2_companion.tgz \
    && docker exec $addon tar xzf /tmp/_fp2_companion.tgz -C /config/apps \
    && docker exec $addon rm /tmp/_fp2_companion.tgz \
    && rm -f $STAGE/_fp2_companion.tgz"
  rm -f "$tarball"
  echo "App: ${#files[@]} file(s) to $addon:/config/apps/fp2_companion"
}

deploy_app
