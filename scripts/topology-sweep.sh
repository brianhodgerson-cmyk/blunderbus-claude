#!/usr/bin/env bash
# Topology sweep — pings all HodgeSpot VMs and checks key services.
# Usage: ./scripts/topology-sweep.sh

set -euo pipefail

declare -A HOSTS=(
  ["Cortex (106)"]="192.168.50.106"
  ["Stark (104)"]="192.168.50.204"
  ["Thor (101)"]="192.168.50.136"
  ["Banner (202)"]="192.168.50.202"
  ["TrueNAS (100)"]="192.168.50.50"
  ["HomeAssistant (102)"]="192.168.50.206"
  ["Fury/SecOnion (103)"]="192.168.50.103"
  ["Loki"]="192.168.50.207"
  ["Frigate"]="192.168.50.205"
)

echo "=== HodgeSpot Topology Sweep ==="
echo "Time: $(date)"
echo ""

printf "%-25s %-18s %-8s\n" "Host" "IP" "Status"
printf "%-25s %-18s %-8s\n" "----" "--" "------"

for name in "${!HOSTS[@]}"; do
  ip="${HOSTS[$name]}"
  if ping -c 1 -W 2 "$ip" > /dev/null 2>&1; then
    printf "%-25s %-18s %-8s\n" "$name" "$ip" "✅ UP"
  else
    printf "%-25s %-18s %-8s\n" "$name" "$ip" "❌ DOWN"
  fi
done

echo ""
echo "=== Service HTTP Checks ==="

declare -A SERVICES=(
  ["Grafana"]="http://192.168.50.202:3000/api/health"
  ["Home Assistant"]="http://192.168.50.206:8123/api/"
  ["Frigate"]="http://192.168.50.205:5000/api/version"
  ["Loki"]="http://192.168.50.207:3100/ready"
  ["Ollama"]="http://192.168.50.136:11434/"
  ["LiteLLM"]="http://192.168.50.106:4000/health"
)

printf "%-20s %-8s\n" "Service" "HTTP"
printf "%-20s %-8s\n" "-------" "----"

for name in "${!SERVICES[@]}"; do
  url="${SERVICES[$name]}"
  code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "$url" 2>/dev/null || echo "000")
  if [[ "$code" == "200" ]]; then
    printf "%-20s %-8s\n" "$name" "✅ $code"
  else
    printf "%-20s %-8s\n" "$name" "❌ $code"
  fi
done
