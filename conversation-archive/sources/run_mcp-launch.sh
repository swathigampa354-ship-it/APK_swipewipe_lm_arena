#!/data/data/com.termux/files/usr/bin/bash
# Safe relaunch for the Swipewipe RE driver: pidfile-based stop (NOT pkill -f, which
# self-matches the exec wrapper and killed the bridge last time), then detached start.
W=$HOME/rea/swipewipe
if [ -f "$W/.driver.pid" ]; then
  P=$(cat "$W/.driver.pid")
  if [ -n "$P" ] && [ -d "/proc/$P" ]; then
    echo "stopping previous driver pid=$P"; kill "$P" 2>/dev/null
    J=$(pgrep -P "$P" java 2>/dev/null | head -1); [ -n "$J" ] && { echo "stopping child java pid=$J"; kill "$J" 2>/dev/null; }
  fi
fi
grep -q '^MemAvailable:' /proc/meminfo && awk '/MemAvailable/{if ($2 < 3200000) {printf "LOW_MEMORY MemAvailable=%d kB - free RAM before launching (jadx heap is 2 GB)\n", $2; exit 3}}' /proc/meminfo || true
cd "$W" || exit 1
setsid nohup python3 run_mcp.py > logs/driver.out 2>&1 < /dev/null &
sleep 2
NEW=$(cat "$W/.driver.pid" 2>/dev/null)
echo "launched pid=${NEW:-?}"; pgrep -fa 'run_mcp.py' | cut -c1-60; tail -2 logs/driver.out
