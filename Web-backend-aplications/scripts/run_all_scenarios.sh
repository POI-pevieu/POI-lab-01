#!/usr/bin/env bash
# Run every demand level x repetition of one app for ONE provisioning scenario.
#
#   ./scripts/run_all_scenarios.sh <app> <label> [extra run_experiment.py args...]
#
# Operator flow (load generator on the HOST, server on the VM):
#   1. host:  ./scripts/provision_vbox.sh "$VM" 2 2048        # VM powered off; sets vCPU/RAM
#   2. host:  VBoxManage startvm "$VM" --type headless
#   3. VM:    loadtest/.venv/bin/python loadtest/vm_agent.py --port 9100 --token SECRET
#             (or: sudo systemctl start vm_agent)
#   4. host:  ./scripts/run_all_scenarios.sh cofre_digital 2cpu-2gb \
#                 --host http://127.0.0.1:8001 --agent http://127.0.0.1:9100 --agent-token SECRET \
#                 --start-server --workers 2
#             (127.0.0.1 works with the NAT port-forwarding rules created by provision_vbox.sh;
#              with a host-only adapter use the VM's IP instead)
#   5. repeat 1-4 for every provisioning label (>= 4). Raw data: results/<app>/<label>/...
#
# Without --agent everything (server, collectors, locust) runs on this machine.
# Pass --dry-run to only print the plan, --resume to continue an interrupted label.
set -euo pipefail
if [[ $# -lt 2 ]]; then sed -n '2,21p' "$0"; exit 1; fi
APP="$1"; LABEL="$2"; shift 2

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO="$REPO/loadtest/scenarios/$APP.yaml"
[[ -f "$SCENARIO" ]] || { echo "scenario not found: $SCENARIO" >&2; exit 1; }

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$REPO/loadtest/.venv/bin/python" ]]; then PY="$REPO/loadtest/.venv/bin/python"; else PY=python3; fi
fi

echo ">> $APP / $LABEL  ($(date -Iseconds))"
exec "$PY" "$REPO/loadtest/run_experiment.py" --scenario "$SCENARIO" --label "$LABEL" "$@"
