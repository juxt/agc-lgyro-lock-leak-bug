# Apollo 11 AGC LGYRO Lock Leak Bug Reproducer

Based on the analysis in ["A Bug on the Dark Side of the Moon"](https://www.juxt.pro/blog/a-bug-on-the-dark-side-of-the-moon/), this repository reproduces a 57-year-old undiscovered bug in the Apollo 11 Lunar Module
flight software (Luminary099), using the [Virtual AGC](https://github.com/virtualagc/repositoryreproduces) emulator running the original code. It identified in 1970 as software anomaly  `L-1D-02` documented [here](https://www.ibiblio.org/apollo/Documents/contents_of_luminary_1d.pdf#page=51) and [here](https://www.ibiblio.org/apollo/Documents/contents_of_luminary_1e.pdf#page=316).


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

  Phase 0: Start yaAGC with Luminary099 flight software
  ----------------------------------------------------------
    [OK   ] yaAGC running in debug mode

  Phase 1: Boot AGC and verify initial state
  ----------------------------------------------------------
    [OK   ] Stepped 5000 MCTs through FRESH START
    [READ ] LGYRO = 0o0
  ✓ [PASS ] LGYRO starts at 0 (gyro lock is free)

  Phase 2: Set up pre-conditions for the bug scenario
  ----------------------------------------------------------
    [INFO ] Scenario: IMUPULSE has just acquired the LGYRO lock
    [INFO ] and scheduled STRTGYRO to torque the gyros.
    [INFO ] Then the crew accidentally bumps the IMU cage switch.
    [SET  ] LGYRO = 0o6001 (lock acquired by IMUPULSE)
    [SET  ] IMODES30 = 0o40 (bit 6: IMU CAGED)
    [SET  ] MODECADR = 0o100 (job waiting for completion)
    [SET  ] Z -> STRTGYRO (bank 07, offset 3405)

  Phase 3: Execute STRTGYRO -> CAGETEST -> BADEND (the bug path)
  ----------------------------------------------------------
    [INFO ] Stepping through original Luminary099 code...

               --- entered STRTGYRO (IMU_MODE_SWITCHING_ROUTINES.agc) ---
           504 EXTEND
           505 WAND	CHAN14
           507 TC	CAGETEST
      >>>  703 CAGETEST	CAF	BIT6		# SUBROUTINE TO TERMINATE IMU MODE
           704 MASK	IMODES30	# SWITCH IF IMU HAS BEEN CAGED.
           705 CCS	A
      >>>  706 TCF	IMUBAD		# DIRECTLY.
      >>>  700 IMUBAD		CAF	ZERO
           701 TCF	BADEND
      >>>  728 BADEND		TS	RUPTREG2	# DEVICE INDEX.
           729 CS	ZERO		# FOR FAILURE.
           730 TCF	GOODEND +2
           735 TS	RUPTREG3
           736 INDEX	RUPTREG2	# SEE IF USING PROGRAM ASLEEP.
      >>>  737 CCS	MODECADR
           738 TCF	+4		# YES - WAKE IT UP.
           744 CAF	ZERO		# WAKE SLEEPING PROGRAM.
           745 INDEX	RUPTREG2
      >>>  746 XCH	MODECADR
           747 TC	JOBWAKE


  Phase 4: Inspect state after BADEND completes
  ----------------------------------------------------------
    [READ ] MODECADR = 0o0
  ✓ [PASS ] MODECADR cleared by BADEND (job woken with failure)
    [READ ] LGYRO    = 0o6001
  ✓ [PASS ] LGYRO = 0o6001 -- NOT cleared by BADEND (THE BUG)

  Phase 5: Demonstrate the consequence: execute IMUPULSE with stuck lock
  ----------------------------------------------------------
    [INFO ] Simulating a new gyro operation after the crew uncages the IMU.
    [INFO ] Redirecting PC to IMUPULSE and stepping through real code...
    [SET  ] IMODES30 = 0 (IMU uncaged, crew recovered)
    [SET  ] A = valid ECADR (new gyro torque request)
    [SET  ] Z -> IMUPULSE (bank 0o7, S-reg 0o3323)

           436 TC	CAGETSTJ	# DONT PROCEED IF IMU BEING CAGED.
           715 CAGETSTJ	CS	IMODES30	# IF DURING MODE SWITCH INITIALIZATION
           716 MASK	BIT6		# IT IS FOUND THAT THE IMU IS BEING CAGED,
           717 CCS	A		# SET IMUCADR TO -0 TO INDICATE OPERATION
           718 TC	Q		# COMPLETE BUT FAILED.  RETURN IMMEDIATELY
      >>>  438 CCS	LGYRO		# SEE IF GYROS BUSY.
      >>>  439 TC	GYROBUSY	# SLEEP.
      >>>  482 GYROBUSY	EXTEND			# SAVE RETURN 2FCADR.
           483 DCA	BUF2
           484 DXCH	MPAC
      >>>  485 REGSLEEP	CAF	LGWAKE

  ✓ [PASS ] CCS LGYRO found lock held (LGYRO=0o6001) -> branched to GYROBUSY
  ✓ [PASS ] GYROBUSY reached JOBSLEEP -- job hangs forever (no one will wake it)

    [INFO ] Dead operations (all silent, no alarm, no DSKY error):
    [INFO ]   P52  IMU realignment (star sighting)
    [INFO ]   1/GYRO  gyro drift compensation
    [INFO ]   V49  manual IMU torquing

  Phase 6: The missing fix
  ----------------------------------------------------------

    [CODE ] STRTGYR2 (normal path) correctly does:
           521    CAF   ZERO
           522    TS    LGYRO       # release lock

    [CODE ] BADEND should do the same but doesn't.
    [CODE ] Fix: add before BADEND's TCF GOODEND+2:

                  CAF   ZERO
                  TS    LGYRO       # release gyro lock on error path

    [INFO ] Two instructions. Four bytes.
    [INFO ] Missing across every Apollo mission, for 57 years.

================================================================
  RESULT: Bug reproduced successfully
================================================================

  Bug:     LGYRO resource lock leak in BADEND
  File:    IMU_MODE_SWITCHING_ROUTINES.agc:728
  Cause:   BADEND clears MODECADR but not LGYRO
  Trigger: IMU cage during active gyro torquing
  Effect:  All gyro operations silently hang forever
  Fix:     Add CAF ZERO / TS LGYRO to BADEND
```

## Verifying the Fix

You can also verify that the proposed two-instruction fix resolves the bug.
This patches the actual AGC source, reassembles with yaYUL, and confirms
LGYRO is properly released and IMUPULSE proceeds without deadlocking:

```bash
python3 reproduce_lgyro_bug.py --verify-fix
```

The source is automatically reverted to the original (buggy) Luminary099
after verification.

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
