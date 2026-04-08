#!/bin/bash
# Clean all build artifacts (yaAGC, yaYUL, and assembled Luminary099)
set -e
cd "$(dirname "$0")"

echo "=== Cleaning yaAGC ==="
make -C virtualagc/yaAGC clean

echo "=== Cleaning yaYUL ==="
make -C virtualagc/yaYUL clean

echo "=== Removing Luminary099 assembly output ==="
rm -f virtualagc/Luminary099/MAIN.agc.bin
rm -f virtualagc/Luminary099/MAIN.agc.symtab

echo "=== Clean complete ==="
