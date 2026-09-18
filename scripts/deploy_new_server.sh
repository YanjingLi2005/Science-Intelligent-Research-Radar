#!/usr/bin/env bash
# Deploy a separate Research Radar stack without touching the existing one.
set -euo pipefail

cd "$(dirname "$0")/.."
profile="${RADAR_PROFILE:-standard}"
case "${profile}" in
  standard|enhanced) ;;
  *) echo "Unsupported RADAR_PROFILE: ${profile}" >&2; exit 2 ;;
esac

export RADAR_IMAGE_REPOSITORY="${RADAR_IMAGE_REPOSITORY:?Set RADAR_IMAGE_REPOSITORY}"
export RADAR_IMAGE_TAG="${RADAR_IMAGE_TAG:?Set RADAR_IMAGE_TAG}"
mkdir -p data

docker compose -f docker-compose.new.yml pull research-radar
docker compose -f docker-compose.new.yml up -d --no-build research-radar

echo "Waiting for the new service on 127.0.0.1:8502 ..."
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8502/api/health; then
    echo "New Research Radar is healthy on 127.0.0.1:8502."
    exit 0
  fi
  sleep 3
done

docker compose -f docker-compose.new.yml ps
echo "New Research Radar did not pass its health check." >&2
exit 1
