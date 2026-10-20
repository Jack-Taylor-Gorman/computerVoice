#!/bin/bash
# Run "$@" once MemAvailable exceeds 3 GB (checked every 20 s, up to 2 hours).
for i in $(seq 1 360); do
  kb=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
  if [ "$kb" -gt 3145728 ]; then echo "mem ok: $kb kB"; exec "$@"; fi
  sleep 20
done
echo "gave up waiting for memory"; exit 3
