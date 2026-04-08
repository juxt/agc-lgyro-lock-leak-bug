#!/usr/bin/env python3
"""
Reproducer for the LGYRO lock leak bug in Apollo 11 AGC (Luminary099).

Bug: When the IMU is caged during active gyro torquing, BADEND clears
MODECADR but not LGYRO. This leaves the gyro resource lock permanently
held, silently deadlocking all future gyro operations.

Reference: "A Bug on the Dark Side of the Moon"
https://deploy-preview-248--juxt-website.netlify.app/blog/a-bug-on-the-dark-side-of-the-moon/

This script drives the yaAGC emulator in debug mode, executing the
original Luminary099 flight software through the bug path and observing
the lock leak in real time.
"""

import os
import re
import subprocess
import sys

import pexpect

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VIRTUALAGC_DIR = os.path.join(SCRIPT_DIR, "virtualagc")
YAAGC = os.path.join(VIRTUALAGC_DIR, "yaAGC", "yaAGC")
YAYUL = os.path.join(VIRTUALAGC_DIR, "yaYUL", "yaYUL")
LUMINARY_DIR = os.path.join(VIRTUALAGC_DIR, "Luminary099")
LUMINARY_BIN = os.path.join(LUMINARY_DIR, "MAIN.agc.bin")
LUMINARY_SYM = os.path.join(LUMINARY_DIR, "MAIN.agc.symtab")
IMU_SOURCE = os.path.join(LUMINARY_DIR, "IMU_MODE_SWITCHING_ROUTINES.agc")
AGC_PORT = 19847

BADEND_BUGGY = ("BADEND\t\tTS\tRUPTREG2\t# DEVICE INDEX.\n"
                "\t\tCS\tZERO\t\t# FOR FAILURE.\n"
                "\t\tTCF\tGOODEND +2")

BADEND_FIXED = ("BADEND\t\tTS\tRUPTREG2\t# DEVICE INDEX.\n"
                "\t\tCAF\tZERO\t\t# RELEASE GYRO LOCK ON ERROR PATH.\n"
                "\t\tTS\tLGYRO\n"
                "\t\tCS\tZERO\t\t# FOR FAILURE.\n"
                "\t\tTCF\tGOODEND +2")

PROMPT = r'\(agc\) '


class AgcDebug:
    """Interface to yaAGC's debug console via pexpect (PTY)."""

    def __init__(self, port=AGC_PORT):
        for path, desc in [(YAAGC, "yaAGC binary"), (LUMINARY_BIN, "Luminary099 binary"),
                           (LUMINARY_SYM, "Symbol table")]:
            if not os.path.exists(path):
                print(f"ERROR: {desc} not found at {path}")
                print("Run ./build.sh first.")
                sys.exit(1)

        cmd = (f"{YAAGC} --exec={LUMINARY_BIN} --symtab={LUMINARY_SYM} "
               f"--port={port} --quiet")
        self.child = pexpect.spawn(cmd, timeout=30, encoding="utf-8")
        self.child.expect(PROMPT)

    def command(self, cmd, timeout=30):
        """Send a debug command, wait for prompt, return output."""
        self.child.sendline(cmd)
        self.child.expect(PROMPT, timeout=timeout)
        return self.child.before

    def set_breakpoint(self, symbol):
        out = self.command(f"break {symbol}")
        return out.strip()

    def read_var(self, symbol):
        """Read a variable by symbol name. Returns integer value (octal)."""
        out = self.command(f"print {symbol}")
        m = re.search(r'\$\d+\s*=\s*(0?\d+)', out)
        if m:
            val_str = m.group(1)
            if val_str.startswith("0") and len(val_str) > 1:
                return int(val_str, 8)
            return int(val_str)
        raise ValueError(f"Could not parse print output: {out!r}")

    def write_var(self, symbol, value):
        """Write a variable by symbol name (value as decimal integer)."""
        return self.command(f"set variable {symbol}={value}")

    def write_register(self, addr, hex_value):
        """Write a CPU register by its erasable address (0-6). Value in hex."""
        return self.command(f"set variable *0x{addr:x}=0x{hex_value:04x}")

    def step(self, count=1):
        return self.command(f"step {oct(count)[2:]}", timeout=120)

    def step_one(self):
        """Step one instruction, return the output line."""
        out = self.command("step")
        lines = [l.strip() for l in out.strip().split('\n')
                 if l.strip() and not l.strip().startswith('step')]
        return lines

    def cont(self, timeout=30):
        return self.command("cont", timeout=timeout)

    def sym_dump(self, symbol):
        """Get symbol address info."""
        out = self.command(f"sym-dump {symbol}")
        return out.strip()

    def resolve_fixed_addr(self, symbol):
        """Resolve a fixed-memory symbol to (bank, sreg) via sym-dump.
        Returns (bank_octal, sreg_octal) for use with set_pc_to."""
        out = self.sym_dump(f"^{symbol}$")
        # Format: "07,3351" for bank 07, S-register 3351 octal
        m = re.search(r'(\d+),(\d+)', out)
        if not m:
            raise ValueError(f"Could not resolve symbol {symbol}: {out!r}")
        bank = int(m.group(1), 8)
        sreg = int(m.group(2), 8)
        return (bank, sreg)

    def set_pc_to(self, bank, sreg):
        """Set the program counter to a fixed-switchable address.
        bank: bank number (integer), sreg: S-register address (integer, octal range 02000-03777)."""
        fb = bank << 10
        bb = fb | 0x06  # EBANK doesn't matter much, use 6 as existing code does
        self.write_register(4, fb)      # FB
        self.write_register(6, bb)      # BB
        self.write_register(5, sreg)    # Z = S-register

    def close(self):
        self.child.terminate(force=True)
        self.child.wait()


def print_header(title):
    print()
    w = 64
    print("=" * w)
    print(f"  {title}")
    print("=" * w)


def print_phase(phase, msg):
    print(f"\n{'':>2}Phase {phase}: {msg}")
    print(f"{'':>2}{'-' * 58}")


def print_line(tag, msg, indent=4):
    print(f"{'':{indent}s}[{tag:5s}] {msg}")


def print_trace(line_num, text, highlight=False):
    marker = ">>>" if highlight else "   "
    print(f"      {marker} {line_num:>4s} {text}")


def print_check(ok, msg, indent=4):
    marker = "\u2713" if ok else "\u2717"
    status = "PASS" if ok else "FAIL"
    print(f"{'':{indent - 2}s}{marker} [{status:5s}] {msg}")
    return ok


def run():
    print_header("LGYRO Lock Leak Bug Reproducer\n  Luminary099 -- Apollo 11 Lunar Module")

    all_pass = True
    dbg = None

    try:
        # ── Phase 0 ─────────────────────────────────────────────
        print_phase(0, "Start yaAGC with Luminary099 flight software")
        dbg = AgcDebug()
        print_line("OK", "yaAGC running in debug mode")

        # ── Phase 1 ─────────────────────────────────────────────
        print_phase(1, "Boot AGC and verify initial state")
        dbg.step(5000)
        print_line("OK", "Stepped 5000 MCTs through FRESH START")

        lgyro_init = dbg.read_var("LGYRO")
        print_line("READ", f"LGYRO = {oct(lgyro_init)}")
        all_pass &= print_check(lgyro_init == 0,
            "LGYRO starts at 0 (gyro lock is free)")

        # ── Phase 2 ─────────────────────────────────────────────
        print_phase(2, "Set up pre-conditions for the bug scenario")
        print_line("INFO", "Scenario: IMUPULSE has just acquired the LGYRO lock")
        print_line("INFO", "and scheduled STRTGYRO to torque the gyros.")
        print_line("INFO", "Then the crew accidentally bumps the IMU cage switch.")

        # IMUPULSE stores an ECADR-derived value in LGYRO to reserve gyros.
        lgyro_held = 0o06001
        dbg.write_var("LGYRO", lgyro_held)
        print_line("SET", f"LGYRO = {oct(lgyro_held)} (lock acquired by IMUPULSE)")

        # Set IMODES30 bit 6 = IMU is being caged
        dbg.write_var("IMODES30", 0o40)
        print_line("SET", f"IMODES30 = {oct(0o40)} (bit 6: IMU CAGED)")

        # Set MODECADR non-zero to simulate a waiting job
        dbg.write_var("MODECADR", 0o100)
        print_line("SET", f"MODECADR = {oct(0o100)} (job waiting for completion)")

        # Redirect execution to STRTGYRO (bank 07, offset 3405 octal)
        # CPU registers: FB=bank 07 (0x1C00), BB=0x1C06, Z=0x0705 (3405 oct)
        dbg.write_register(4, 0x1C00)   # FB = bank 07
        dbg.write_register(6, 0x1C06)   # BB
        dbg.write_register(5, 0x0705)   # Z = STRTGYRO native address
        print_line("SET", "Z -> STRTGYRO (bank 07, offset 3405)")

        # ── Phase 3 ─────────────────────────────────────────────
        print_phase(3, "Execute STRTGYRO -> CAGETEST -> BADEND (the bug path)")
        print_line("INFO", "Stepping through original Luminary099 code...")
        print()

        # Step through the bug path instruction by instruction
        badend_reached = False
        badend_done = False
        max_steps = 40
        for i in range(max_steps):
            lines = dbg.step_one()
            for l in lines:
                # Determine highlight for key moments
                highlight = False
                if "CAGETEST" in l and "CAF" in l:
                    highlight = True
                elif "IMUBAD" in l:
                    highlight = True
                elif "BADEND" in l and "TS" in l:
                    highlight = True
                    badend_reached = True
                elif "MODECADR" in l:
                    highlight = True
                elif "TASKOVER" in l or "JOBWAKE" in l:
                    badend_done = True

                # Extract line number and text
                m = re.match(r'(\w+)\s+\(\)\s+at\s+(\S+):(\d+)', l)
                if m:
                    print_trace("", f"--- entered {m.group(1)} ({m.group(2)}) ---",
                                highlight=("BADEND" in l or "IMUBAD" in l))
                    continue

                m2 = re.match(r'(\d+)\s+(.*)', l)
                if m2:
                    print_trace(m2.group(1), m2.group(2), highlight=highlight)

            if badend_done:
                break

        print()

        # ── Phase 4 ─────────────────────────────────────────────
        print_phase(4, "Inspect state after BADEND completes")

        lgyro_after = dbg.read_var("LGYRO")
        modecadr_after = dbg.read_var("MODECADR")

        print_line("READ", f"MODECADR = {oct(modecadr_after)}")
        all_pass &= print_check(modecadr_after == 0,
            "MODECADR cleared by BADEND (job woken with failure)")

        print_line("READ", f"LGYRO    = {oct(lgyro_after)}")
        all_pass &= print_check(lgyro_after != 0,
            f"LGYRO = {oct(lgyro_after)} -- NOT cleared by BADEND (THE BUG)")

        # ── Phase 5 ─────────────────────────────────────────────
        print_phase(5, "Demonstrate the consequence: execute IMUPULSE with stuck lock")

        print_line("INFO", "Simulating a new gyro operation after the crew uncages the IMU.")
        print_line("INFO", "Redirecting PC to IMUPULSE and stepping through real code...")

        # Clear the cage bit — crew has uncaged the IMU
        dbg.write_var("IMODES30", 0)
        print_line("SET", "IMODES30 = 0 (IMU uncaged, crew recovered)")

        # Set A register to a valid ECADR (needed by IMUPULSE's first TS)
        dbg.write_register(0, 0x0040)  # A = some valid ECADR
        print_line("SET", "A = valid ECADR (new gyro torque request)")

        # Resolve IMUPULSE address and redirect PC there
        imp_bank, imp_sreg = dbg.resolve_fixed_addr("IMUPULSE")
        dbg.set_pc_to(imp_bank, imp_sreg)
        print_line("SET", f"Z -> IMUPULSE (bank {oct(imp_bank)}, S-reg {oct(imp_sreg)})")
        print()

        # Step through IMUPULSE and watch it hit GYROBUSY -> JOBSLEEP
        reached_gyrobusy = False
        reached_jobsleep = False
        took_gyrobusy_branch = False
        max_steps = 25
        for i in range(max_steps):
            lines = dbg.step_one()
            for l in lines:
                highlight = False
                if "GYROBUSY" in l:
                    reached_gyrobusy = True
                    highlight = True
                if "JOBSLEEP" in l or "REGSLEEP" in l:
                    reached_jobsleep = True
                    highlight = True
                if "CCS" in l and "LGYRO" in l:
                    highlight = True
                if "TC" in l and "GYROBUSY" in l:
                    took_gyrobusy_branch = True
                    highlight = True

                m = re.match(r'(\w+)\s+\(\)\s+at\s+(\S+):(\d+)', l)
                if m:
                    print_trace("", f"--- entered {m.group(1)} ({m.group(2)}) ---",
                                highlight=("GYROBUSY" in l))
                    continue

                m2 = re.match(r'(\d+)\s+(.*)', l)
                if m2:
                    print_trace(m2.group(1), m2.group(2), highlight=highlight)

            if reached_jobsleep:
                break

        print()
        lgyro_val = dbg.read_var("LGYRO")
        all_pass &= print_check(took_gyrobusy_branch,
            f"CCS LGYRO found lock held (LGYRO={oct(lgyro_val)}) -> branched to GYROBUSY")
        all_pass &= print_check(reached_jobsleep,
            "GYROBUSY reached JOBSLEEP/REGSLEEP -- job hangs forever (no one will wake it)")

        print()
        print_line("INFO", "Dead operations (all silent, no alarm, no DSKY error):")
        print_line("INFO", "  P52  IMU realignment (star sighting)")
        print_line("INFO", "  1/GYRO  gyro drift compensation")
        print_line("INFO", "  V49  manual IMU torquing")

        # ── Phase 6 ─────────────────────────────────────────────
        print_phase(6, "The missing fix")
        print()
        print_line("CODE", "STRTGYR2 (normal path) correctly does:")
        print_trace("521", "   CAF   ZERO")
        print_trace("522", "   TS    LGYRO       # release lock")
        print()
        print_line("CODE", "BADEND should do the same but doesn't.")
        print_line("CODE", "Fix: add before BADEND's TCF GOODEND+2:")
        print()
        print_trace("", "   CAF   ZERO")
        print_trace("", "   TS    LGYRO       # release gyro lock on error path")
        print()
        print_line("INFO", "Two instructions. Four bytes.")
        print_line("INFO", "Missing across every Apollo mission, for 57 years.")

    finally:
        if dbg:
            dbg.close()

    # ── Summary ──────────────────────────────────────────────────
    print()
    w = 64
    print("=" * w)
    if all_pass:
        print("  RESULT: Bug reproduced successfully")
    else:
        print("  RESULT: Some checks failed (see above)")
    print("=" * w)
    print()
    print("  Bug:     LGYRO resource lock leak in BADEND")
    print("  File:    IMU_MODE_SWITCHING_ROUTINES.agc:728")
    print("  Cause:   BADEND clears MODECADR but not LGYRO")
    print("  Trigger: IMU cage during active gyro torquing")
    print("  Effect:  All gyro operations silently hang forever")
    print("  Fix:     Add CAF ZERO / TS LGYRO to BADEND")
    print()

    return 0 if all_pass else 1


def patch_source(apply_fix):
    """Patch BADEND in the AGC source and reassemble. Returns True on success."""
    if not os.path.isfile(YAYUL) or not os.access(YAYUL, os.X_OK):
        print(f"ERROR: yaYUL assembler not found or not executable at {YAYUL}")
        print("Run ./build.sh first.")
        return False

    with open(IMU_SOURCE, "r") as f:
        source = f.read()

    if apply_fix:
        if BADEND_FIXED in source:
            return True  # already patched
        if BADEND_BUGGY not in source:
            print("ERROR: Could not find BADEND buggy pattern in source")
            return False
        source = source.replace(BADEND_BUGGY, BADEND_FIXED, 1)
    else:
        if BADEND_BUGGY in source:
            return True  # already original
        if BADEND_FIXED not in source:
            print("ERROR: Could not find BADEND fixed pattern in source")
            return False
        source = source.replace(BADEND_FIXED, BADEND_BUGGY, 1)

    with open(IMU_SOURCE, "w") as f:
        f.write(source)

    # Reassemble
    try:
        result = subprocess.run(
            [YAYUL, "--force", "MAIN.agc"],
            cwd=LUMINARY_DIR,
            capture_output=True, text=True, timeout=120)
    except OSError as e:
        print(f"ERROR: Failed to run yaYUL: {e}")
        return False
    if result.returncode != 0:
        print(f"ERROR: yaYUL failed:\n{result.stderr}")
        return False

    # Check for errors
    for line in result.stdout.splitlines():
        if "Fatal errors:" in line and int(line.split(":")[-1].strip()) != 0:
            print(f"ERROR: Assembly errors: {line}")
            return False
    return True


def verify_fix():
    """Apply the fix, reassemble, and verify the bug is gone."""
    print_header("LGYRO Fix Verification\n  Patching BADEND and reassembling Luminary099")

    all_pass = True
    dbg = None

    try:
        # ── Patch and rebuild ───────────────────────────────────
        print_phase(1, "Apply fix to BADEND and reassemble")
        if not patch_source(apply_fix=True):
            return 1
        print_line("OK", "Patched BADEND: added CAF ZERO / TS LGYRO")
        print_line("OK", "Reassembled Luminary099 with fix")

        # ── Boot ────────────────────────────────────────────────
        print_phase(2, "Boot patched AGC and set up bug scenario")
        dbg = AgcDebug()
        dbg.step(5000)
        print_line("OK", "yaAGC running with patched Luminary099")

        dbg.write_var("LGYRO", 0o06001)
        dbg.write_var("IMODES30", 0o40)
        dbg.write_var("MODECADR", 0o100)
        imp_bank, imp_sreg = dbg.resolve_fixed_addr("STRTGYRO")
        dbg.set_pc_to(imp_bank, imp_sreg)
        print_line("SET", "Same pre-conditions as bug repro (LGYRO held, IMU caged)")
        print_line("SET", "Z -> STRTGYRO")

        # ── Execute BADEND (now with fix) ───────────────────────
        print_phase(3, "Execute STRTGYRO -> CAGETEST -> patched BADEND")
        print_line("INFO", "Stepping through patched code...")
        print()

        badend_done = False
        for i in range(40):
            lines = dbg.step_one()
            for l in lines:
                highlight = False
                if "LGYRO" in l:
                    highlight = True
                if "BADEND" in l:
                    highlight = True
                if "TASKOVER" in l or "JOBWAKE" in l:
                    badend_done = True

                m = re.match(r'(\w+)\s+\(\)\s+at\s+(\S+):(\d+)', l)
                if m:
                    print_trace("", f"--- entered {m.group(1)} ---",
                                highlight=("BADEND" in l))
                    continue
                m2 = re.match(r'(\d+)\s+(.*)', l)
                if m2:
                    print_trace(m2.group(1), m2.group(2), highlight=highlight)
            if badend_done:
                break

        print()

        # ── Check state ────────────────────────────────────────
        print_phase(4, "Inspect state after patched BADEND")

        lgyro_after = dbg.read_var("LGYRO")
        modecadr_after = dbg.read_var("MODECADR")

        print_line("READ", f"MODECADR = {oct(modecadr_after)}")
        all_pass &= print_check(modecadr_after == 0,
            "MODECADR cleared by BADEND")

        print_line("READ", f"LGYRO    = {oct(lgyro_after)}")
        all_pass &= print_check(lgyro_after == 0,
            "LGYRO cleared by patched BADEND (fix works!)")

        # ── Verify IMUPULSE proceeds ────────────────────────────
        print_phase(5, "Verify IMUPULSE proceeds normally (no deadlock)")

        dbg.write_var("IMODES30", 0)
        dbg.write_register(0, 0x0040)
        imp_bank, imp_sreg = dbg.resolve_fixed_addr("IMUPULSE")
        dbg.set_pc_to(imp_bank, imp_sreg)
        print_line("SET", "Z -> IMUPULSE (IMU uncaged, LGYRO free)")
        print()

        passed_ccs = False
        reached_gyrobusy = False
        proceeded_normally = False
        for i in range(25):
            lines = dbg.step_one()
            for l in lines:
                highlight = False
                if "CCS" in l and "LGYRO" in l:
                    passed_ccs = True
                    highlight = True
                if "GYROBUSY" in l:
                    reached_gyrobusy = True
                if "MPAC" in l and "TS" in l and passed_ccs:
                    proceeded_normally = True
                    highlight = True
                if "WAITLIST" in l:
                    proceeded_normally = True

                m = re.match(r'(\w+)\s+\(\)\s+at\s+(\S+):(\d+)', l)
                if m:
                    print_trace("", f"--- entered {m.group(1)} ---",
                                highlight=False)
                    continue
                m2 = re.match(r'(\d+)\s+(.*)', l)
                if m2:
                    print_trace(m2.group(1), m2.group(2), highlight=highlight)

            if proceeded_normally:
                break

        print()
        all_pass &= print_check(passed_ccs,
            "CCS LGYRO executed")
        all_pass &= print_check(not reached_gyrobusy,
            "GYROBUSY NOT reached (lock is free)")
        all_pass &= print_check(proceeded_normally,
            "IMUPULSE proceeded to schedule gyro torque normally")

    finally:
        if dbg:
            dbg.close()
        # Revert the source
        patch_source(apply_fix=False)
        print()
        print_line("OK", "Reverted source to original (buggy) Luminary099")

    print()
    w = 64
    print("=" * w)
    if all_pass:
        print("  RESULT: Fix verified -- BADEND now releases LGYRO")
    else:
        print("  RESULT: Fix verification failed (see above)")
    print("=" * w)
    print()

    return 0 if all_pass else 1


if __name__ == "__main__":
    if "--verify-fix" in sys.argv:
        sys.exit(verify_fix())
    sys.exit(run())
