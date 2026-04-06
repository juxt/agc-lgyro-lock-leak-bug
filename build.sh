#!/bin/bash
# Build the AGC emulator and assemble Luminary099
set -e
cd "$(dirname "$0")"

echo "=== Initializing virtualagc submodule ==="
git submodule update --init --depth 1

echo "=== Building yaAGC (emulator) ==="
make -C virtualagc/yaAGC cc=gcc

echo "=== Building yaYUL (assembler) ==="
make -C virtualagc/yaYUL cc=gcc

echo "=== Assembling Luminary099 (Apollo 11 LM) ==="
cd virtualagc/Luminary099
../yaYUL/yaYUL --force MAIN.agc > /dev/null 2>&1

echo "=== Build complete ==="
echo "  yaAGC:       virtualagc/yaAGC/yaAGC"
echo "  Luminary099: virtualagc/Luminary099/MAIN.agc.bin"
echo "  Symbols:     virtualagc/Luminary099/MAIN.agc.symtab"
