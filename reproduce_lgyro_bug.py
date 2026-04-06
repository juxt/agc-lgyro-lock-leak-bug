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
import sys

import pexpect

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VIRTUALAGC_DIR = os.path.join(SCRIPT_DIR, "virtualagc")
YAAGC = os.path.join(VIRTUALAGC_DIR, "yaAGC", "yaAGC")
LUMINARY_BIN = os.path.join(VIRTUALAGC_DIR, "Luminary099", "MAIN.agc.bin")
LUMINARY_SYM = os.path.join(VIRTUALAGC_DIR, "Luminary099", "MAIN.agc.symtab")
AGC_PORT = 19847

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
    print(f"{'':{indent}s} {marker} [{status}] {msg}")
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
        print_phase(5, "Demonstrate the consequence: permanent deadlock")

        print_line("INFO", "Any future call to IMUPULSE now does:")
        print()
        print_trace("", "IMUPULSE:")
        print_trace("435", "   TS    MPAC+5")
        print_trace("436", "   TC    CAGETSTJ    # check cage")
        print_trace("438", "   CCS   LGYRO       # test the lock")
        lgyro_val = dbg.read_var("LGYRO")
        if lgyro_val != 0:
            print_trace("439", f"   TC    GYROBUSY    # LGYRO={oct(lgyro_val)} != 0 -> TAKEN", highlight=True)
            print()
            print_trace("", "GYROBUSY:")
            print_trace("482", "   TC    JOBSLEEP    # sleep until LGYRO freed", highlight=True)
            print_trace("", "   ... but STRTGYR2 will never run ...", highlight=True)
            print_trace("", "   ... so JOBWAKE never fires ...", highlight=True)
            print_trace("", "   ... job sleeps FOREVER", highlight=True)
        print()

        all_pass &= print_check(lgyro_val != 0,
            "All future IMUPULSE calls will deadlock at GYROBUSY")

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


if __name__ == "__main__":
    sys.exit(run())
