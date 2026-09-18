#!/usr/bin/env bash
# Deploy a prebuilt Research Radar image to the local Docker Compose stack.
# CI copies this script and docker-compose.yml to the server before running it.
# Local image builds are intentionally not part of the production deploy path.
set -euo pipefail

cd "$(dirname "$0")/.."

profile="${RADAR_PROFILE:-standard}"
case "${profile}" in
  standard|enhanced) ;;
  *) echo "Unsupported RADAR_PROFILE: ${profile} (use standard or enhanced)." >&2; exit 2 ;;
esac

image_tag="${RADAR_IMAGE_TAG:?Set RADAR_IMAGE_TAG to a published image tag (for example sha-<commit-sha>-standard).}"
export RADAR_IMAGE_TAG="${image_tag}"

echo "==> Image profile: ${profile} (${RADAR_IMAGE_REPOSITORY:-ghcr.io/gz-november/research-radar}:${image_tag})"

# Pull the immutable image built by GitHub Actions. Dependency installation
# happens in CI; the server only downloads the resulting image layers.
# Pulls are retried because the registry can drop a transfer under load.
pull_ok=0
for attempt in 1 2 3 4; do
  echo "==> Pulling image (attempt ${attempt}/4) ..."
  if docker compose pull research-radar; then
    pull_ok=1
    break
  fi
  echo "==> Pull attempt ${attempt} failed; retrying in 10s..."
  sleep 10
done
if [ "${pull_ok}" != "1" ]; then
  echo "==> Failed to pull image after 4 attempts."
  exit 1
fi

docker compose up -d --no-build --remove-orphans
echo "==> Current containers:"
docker compose ps || true

echo "==> Waiting for app health check (up to 180s) ..."
healthy=0
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8501/api/health 2>/dev/null || \
     curl -fsS -o /dev/null http://127.0.0.1/api/health 2>/dev/null; then
    echo "==> App is healthy."
    healthy=1
    break
  fi
  sleep 3
done

if [ "${healthy}" != "1" ]; then
  echo "==> App did not pass health check in time."
  exit 1
fi

# Remove unused images older than seven days, including old SHA-tagged releases.
# Run cleanup only after a successful health check so a failed deployment does
# not also remove older images that may be needed for rollback. Docker retains
# images referenced by any container, including the newly deployed one.
docker image prune --all --force --filter "until=168h" >/dev/null 2>&1 || true
