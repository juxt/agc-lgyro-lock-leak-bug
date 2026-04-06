# Apollo 11 AGC LGYRO Lock Leak Bug Reproducer

Based on the analysis in ["A Bug on the Dark Side of the Moon"](https://deploy-preview-248--juxt-website.netlify.app/blog/a-bug-on-the-dark-side-of-the-moon/), this repository reproduces a 57-year-old undiscovered bug in the Apollo 11 Lunar Module
flight software (Luminary099), using the [Virtual AGC](https://github.com/virtualagc/repositoryreproduces) emulator running the original code.


## The Bug

When the IMU is caged during active gyro torquing, the error path
(`BADEND`) clears `MODECADR` but **not** `LGYRO`. The gyro resource lock
remains permanently held, silently deadlocking all future gyro operations.

**Normal path** (`STRTGYR2`, line 521):
```
CAF   ZERO
TS    LGYRO       # releases the lock
```

**Error path** (`BADEND`, line 728): clears `MODECADR` only. `LGYRO` is
never touched. Two missing instructions, four bytes.

**Consequence**: `P52` (IMU realignment), `1/GYRO` (drift compensation),
and all other gyro operations hang forever at `GYROBUSY` with no alarm
and no DSKY error indication.

## How the Reproducer Works

The script drives `yaAGC` (the Virtual AGC emulator) in debug mode:

1. Boots the AGC through `FRESH START` and verifies `LGYRO = 0`
2. Sets up pre-conditions: `LGYRO` held (simulating `IMUPULSE` lock
   acquisition), `IMODES30` bit 6 set (IMU caged)
3. Redirects the program counter to `STRTGYRO` and single-steps through
   the **actual Luminary099 machine code**:
   `STRTGYRO` → `CAGETEST` → `IMUBAD` → `BADEND` → `GOODEND+2`
4. Inspects state: `MODECADR = 0` (cleared), `LGYRO = 06001` (still held!)
5. Shows that subsequent `IMUPULSE` calls deadlock at `GYROBUSY`

## Prerequisites

- GCC (or Clang)
- GNU Make
- Python 3 with `pexpect` (`pip install pexpect`)
- Git

## Quick Start

```bash
git clone --recurse-submodules <this-repo>
cd agc
./build.sh
python3 reproduce_lgyro_bug.py
```

If you've already cloned without `--recurse-submodules`:

```bash
./build.sh    # handles git submodule init
python3 reproduce_lgyro_bug.py
```

## Expected Output

```
================================================================
  LGYRO Lock Leak Bug Reproducer
  Luminary099 -- Apollo 11 Lunar Module
================================================================

  Phase 1: Boot AGC and verify initial state
     ✓ [PASS] LGYRO starts at 0 (gyro lock is free)

  Phase 3: Execute STRTGYRO -> CAGETEST -> BADEND (the bug path)
       503 STRTGYRO  CS   GDESELCT
       507           TC   CAGETEST
   >>> 703 CAGETEST  CAF  BIT6
       704           MASK IMODES30
       706           TCF  IMUBAD
   >>> 700 IMUBAD    CAF  ZERO
       701           TCF  BADEND
   >>> 728 BADEND    TS   RUPTREG2
       ...
   >>> 746           XCH  MODECADR    # clears MODECADR
       747           TC   JOBWAKE     # ... but LGYRO untouched!

  Phase 4: Inspect state after BADEND completes
     ✓ [PASS] MODECADR cleared by BADEND (job woken with failure)
     ✓ [PASS] LGYRO = 0o6001 -- NOT cleared by BADEND (THE BUG)

     ✓ [PASS] All future IMUPULSE calls will deadlock at GYROBUSY

================================================================
  RESULT: Bug reproduced successfully
================================================================
```

## Repository Structure

```
agc/
  reproduce_lgyro_bug.py   # Main reproducer script
  build.sh                 # Builds yaAGC + assembles Luminary099
  README.md                # This file
  virtualagc/              # Git submodule: Virtual AGC project
```

## Key Source Files (in virtualagc/Luminary099/)

| File | Contents |
|------|----------|
| `IMU_MODE_SWITCHING_ROUTINES.agc` | `IMUPULSE`, `STRTGYRO`, `STRTGYR2`, `CAGETEST`, `BADEND`, `GYROBUSY` |
| `IMU_COMPENSATION_PACKAGE.agc` | `1/GYRO` (calls `IMUPULSE` for drift compensation) |
| `T4RUPT_PROGRAM.agc` | `IMUMON`, `IMUCAGE` (cage detection in T4 interrupt) |
| `ERASABLE_ASSIGNMENTS.agc` | `LGYRO`, `IMODES30`, `MODECADR` definitions |
