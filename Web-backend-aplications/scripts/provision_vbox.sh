#!/usr/bin/env bash
# Change vCPU / RAM of a POWERED-OFF VirtualBox VM and (re)create NAT port forwards.
#
#   ./scripts/provision_vbox.sh <vm-name> <cpus> <memory-MB> [--no-forward] [--cpu-cap PCT] [--hostonly IF]
#   ./scripts/provision_vbox.sh ubuntu-srv 2 2048
#
# NAT rules created on adapter 1 (host 127.0.0.1:PORT -> guest PORT):
#   ssh 2222->22, 8001, 8002, 8003 (apps), 9100 (vm_agent)
# --hostonly vboxnet0: also attach adapter 2 to that host-only network. The VirtualBox
#   NAT engine relays every packet in a user-space process on the host and can become
#   the bottleneck at high demand; via host-only, load goes straight to http://VM_IP:PORT
#   (find VM_IP inside the VM with `ip -4 addr`; the interface must exist:
#   VBoxManage hostonlyif create / list hostonlyifs).
# Prints the resulting configuration; record it as the scenario label (e.g. 2cpu-2gb).
set -euo pipefail
if [[ $# -lt 3 ]]; then sed -n '2,14p' "$0"; exit 1; fi
VM="$1"; CPUS="$2"; MEM="$3"; shift 3
FORWARD=1; CAP=""; HOSTONLY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-forward) FORWARD=0; shift ;;
    --cpu-cap) CAP="$2"; shift 2 ;;
    --hostonly) HOSTONLY="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done
command -v VBoxManage >/dev/null || { echo "VBoxManage not found (install VirtualBox)" >&2; exit 1; }

STATE="$(VBoxManage showvminfo "$VM" --machinereadable | sed -n 's/^VMState="\(.*\)"/\1/p')"
if [[ "$STATE" != "poweroff" && "$STATE" != "aborted" && "$STATE" != "saved" ]]; then
  echo "VM '$VM' is '$STATE' - shut it down first: VBoxManage controlvm \"$VM\" acpipowerbutton" >&2
  exit 1
fi
if [[ "$STATE" == "saved" ]]; then
  echo "VM is in 'saved' state; discard it first: VBoxManage discardstate \"$VM\"" >&2
  exit 1
fi

HOST_CPUS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo '?')"
echo ">> $VM: cpus=$CPUS memory=${MEM}MB (host logical CPUs: $HOST_CPUS)"
VBoxManage modifyvm "$VM" --cpus "$CPUS" --memory "$MEM"
if [[ -n "$CAP" ]]; then VBoxManage modifyvm "$VM" --cpuexecutioncap "$CAP"; fi
if [[ -n "$HOSTONLY" ]]; then
  VBoxManage modifyvm "$VM" --nic2 hostonly --hostonlyadapter2 "$HOSTONLY"
  echo ">> adapter 2: host-only on $HOSTONLY (use http://VM_IP:<port> as --host/--agent)"
fi

if [[ $FORWARD -eq 1 ]]; then
  for rule in "ssh,tcp,127.0.0.1,2222,,22" "cofre,tcp,127.0.0.1,8001,,8001" \
              "galeria,tcp,127.0.0.1,8002,,8002" "bolsa,tcp,127.0.0.1,8003,,8003" \
              "agent,tcp,127.0.0.1,9100,,9100"; do
    name="${rule%%,*}"
    VBoxManage modifyvm "$VM" --natpf1 delete "$name" 2>/dev/null || true
    VBoxManage modifyvm "$VM" --natpf1 "$rule"
  done
  echo ">> NAT forwards: 127.0.0.1:{2222->22, 8001, 8002, 8003, 9100}"
fi

VBoxManage showvminfo "$VM" --machinereadable | grep -E '^(cpus|memory|cpuexecutioncap|nic2|hostonlyadapter2|Forwarding)' || true
# exact label: 2048 -> 2gb, 1536 -> 1.5gb, 768 -> 768mb (never rounds two scenarios into one name)
if (( MEM % 1024 == 0 )); then GB="$(( MEM / 1024 ))gb"
elif (( MEM % 512 == 0 && MEM > 1024 )); then GB="$(( MEM / 1024 )).5gb"
else GB="${MEM}mb"; fi
echo ">> suggested label: ${CPUS}cpu-${GB}"
echo ">> start: VBoxManage startvm \"$VM\" --type headless"
