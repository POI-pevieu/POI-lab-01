#!/usr/bin/env bash
# Prepare an Ubuntu Server VM to serve the apps and collect metrics.
#
#   ./scripts/setup_vm.sh                       # run from inside a copy of the repo
#   ./scripts/setup_vm.sh --repo-url URL [--dest ~/Web-backend-aplications]
#   ./scripts/setup_vm.sh --install-systemd     # also install systemd units (needs sudo)
#
# What it does:
#   1. apt install python3-venv python3-pip sysstat git curl
#   2. clone the repo (only with --repo-url and when not already inside it)
#   3. one venv per app: apps/<app>/.venv  (apps/<app>/requirements.txt)
#   4. loadtest venv:    loadtest/.venv    (psutil, pyyaml, ... for vm_agent/monitor)
#   5. optionally install scripts/systemd/*.service with paths/user filled in
set -euo pipefail

REPO_URL=""
DEST="$HOME/Web-backend-aplications"
INSTALL_SYSTEMD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --install-systemd) INSTALL_SYSTEMD=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

APPS=(cofre_digital galeria_filtros bolsa_ficticia)

echo ">> apt packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip sysstat git curl

# locate repo
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/../CONTRACT.md" ]]; then
  REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [[ -n "$REPO_URL" ]]; then
  if [[ ! -d "$DEST/.git" ]]; then git clone "$REPO_URL" "$DEST"; else git -C "$DEST" pull --ff-only; fi
  REPO="$DEST"
else
  echo "not inside the repo and no --repo-url given (or copy the repo with scp/rsync first)" >&2
  exit 1
fi
echo ">> repo: $REPO"

for app in "${APPS[@]}"; do
  d="$REPO/apps/$app"
  if [[ ! -f "$d/requirements.txt" ]]; then echo "!! $d/requirements.txt missing - skipping $app"; continue; fi
  echo ">> venv for $app"
  python3 -m venv --clear "$d/.venv"   # --clear: discard venvs copied from another machine/OS
  "$d/.venv/bin/pip" install -q --upgrade pip
  "$d/.venv/bin/pip" install -q -r "$d/requirements.txt"
done

echo ">> loadtest venv"
python3 -m venv --clear "$REPO/loadtest/.venv"
"$REPO/loadtest/.venv/bin/pip" install -q --upgrade pip
"$REPO/loadtest/.venv/bin/pip" install -q -r "$REPO/loadtest/requirements.txt"

if [[ $INSTALL_SYSTEMD -eq 1 ]]; then
  echo ">> systemd units"
  for unit in "$REPO"/scripts/systemd/*.service; do
    name="$(basename "$unit")"
    sed -e "s|__REPO__|$REPO|g" -e "s|__USER__|$USER|g" "$unit" | sudo tee "/etc/systemd/system/$name" >/dev/null
    echo "   installed /etc/systemd/system/$name (not enabled)"
  done
  sudo systemctl daemon-reload
fi

cat <<MSG

Done. Facts for your notes:
  nproc=$(nproc)  mem=$(free -m | awk '/Mem:/{print $2}')MB  swap=$(free -m | awk '/Swap:/{print $2}')MB
  sysstat: $(mpstat -V 2>&1 | head -1)

Next (on the VM):
  $REPO/loadtest/.venv/bin/python $REPO/loadtest/vm_agent.py --port 9100 --token SECRET
  # or: sudo systemctl start vm_agent   (after --install-systemd)
Then on the HOST:
  ./scripts/run_all_scenarios.sh <app> <label> --agent http://VM_IP:9100 --agent-token SECRET --host http://VM_IP:<port>
MSG
