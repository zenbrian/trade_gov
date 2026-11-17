#!/bin/sh
set -eu

# Railway mounts the persistent volume at runtime, not at image build time.
if [ "${RAILWAY_VOLUME_MOUNT_PATH:-}" != "/data" ] || [ ! -d /data ] || [ ! -w /data ]; then
    echo 'Attach a writable Railway Volume at /data before starting this worker.' >&2
    echo 'For local Docker, mount /data and set RAILWAY_VOLUME_MOUNT_PATH=/data.' >&2
    exit 1
fi

cd /app
exec python -u scripts/trade_batch.py data/input_gc453.csv /data/output_all.csv --resume
