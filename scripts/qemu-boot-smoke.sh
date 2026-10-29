#!/bin/bash
# Bounded BIOS/UEFI boot smoke for the finished DclawOS 2.0 ISO.
# Adapted from the old DclawOS smoke script (same monitor/sendkey/poll
# skeleton), with the gate changed for the new session stack:
#
# Gate (all three must hold):
#   1. A screendump frame passes the generic non-blank visual check
#      (validate-qemu-visual-frame.py) — proves a graphical scene rendered.
#   2. The guest serial console contains the "dclawos-session" marker.
#      The Openbox autostart logs it via `logger` to the system journal,
#      which journald forwards to the serial console (see build.yml
#      --bootappend-live). The autostart only runs after LightDM autologin
#      succeeds, so the marker proves: boot -> autologin -> session ->
#      autostart -> shell launch chain completed. A LightDM greeter frame
#      alone (autologin failed) never produces this marker.
#   3. QEMU stays alive past the budget (rc 124) with no boot-failure
#      diagnostics in its own log.
#
# Serial log is diagnostic-first: on failure the script dumps marker hits
# and a tail of the guest console.
set -euo pipefail

usage() {
    echo "usage: $0 ISO_PATH bios|uefi [seconds]" >&2
    exit 2
}

[ "$#" -ge 2 ] || usage
ISO="$1"
MODE="$2"
BUDGET="${3:-260}"
MIN_DWELL="${DCLAWOS_QEMU_MIN_DWELL:-60}"
POLL_INTERVAL="${DCLAWOS_QEMU_POLL_INTERVAL:-15}"
MONITOR_WAIT=20
BOOT_MENU_WAIT=8
POLL_MARGIN=30
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
VISUAL_VALIDATOR="$SCRIPT_DIR/validate-qemu-visual-frame.py"
START_EPOCH="$(date +%s)"
[ -s "$ISO" ] || { echo "ISO is missing or empty: $ISO" >&2; exit 1; }
case "$MODE" in bios|uefi) ;; *) usage ;; esac
if [ "$BUDGET" -lt $((MONITOR_WAIT + BOOT_MENU_WAIT + MIN_DWELL + POLL_INTERVAL + POLL_MARGIN)) ]; then
    echo "QEMU timeout ${BUDGET}s is too short for the required boot/poll budget" >&2
    exit 2
fi
for command in qemu-system-x86_64 timeout socat python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "required command is unavailable: $command" >&2
        exit 1
    }
done

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
MONITOR="$WORK/monitor.sock"
FRAME="${DCLAWOS_QEMU_SMOKE_FRAME:-${ISO}.${MODE}.ppm}"
LOG="$WORK/qemu.log"
SERIAL_LOG="$WORK/serial-guest.log"
rm -f "$FRAME"

QEMU=(qemu-system-x86_64 -m 2048 -smp 2 -accel tcg -no-reboot -no-shutdown \
      -vga virtio -display none -monitor "unix:${MONITOR},server=on,wait=off" -serial "file:${SERIAL_LOG}" \
      -cdrom "$ISO" -boot d)
if [ "$MODE" = uefi ]; then
    if [ -n "${OVMF_CODE:-}" ]; then
        OVMF_IMAGE="$OVMF_CODE"
    elif [ -s /usr/share/OVMF/OVMF_CODE.fd ]; then
        OVMF_IMAGE=/usr/share/OVMF/OVMF_CODE.fd
    else
        OVMF_IMAGE=/usr/share/ovmf/OVMF.fd
    fi
    [ -s "$OVMF_IMAGE" ] || { echo "OVMF firmware not found: $OVMF_IMAGE" >&2; exit 1; }
    QEMU+=(-machine q35 -bios "$OVMF_IMAGE")
fi

set +e
timeout --foreground "${BUDGET}s" "${QEMU[@]}" 2>"$LOG" &
TIMEOUT_PID=$!
set -e

for _ in $(seq 1 "$MONITOR_WAIT"); do
    [ -S "$MONITOR" ] && break
    sleep 1
done
[ -S "$MONITOR" ] || {
    cat "$LOG" >&2 || true
    echo "QEMU $MODE did not expose a monitor socket" >&2
    exit 1
}
sleep "$BOOT_MENU_WAIT"
printf 'sendkey ret\n' | socat - UNIX-CONNECT:"$MONITOR" >/dev/null 2>&1 || {
    cat "$LOG" >&2 || true
    echo "QEMU $MODE could not select the default Live entry" >&2
    exit 1
}
DEADLINE_EPOCH=$((START_EPOCH + BUDGET - POLL_MARGIN))
FRAME_OK=0
MARKER_OK=0
LAST_CHECK_LOG="$WORK/frame-check.log"
sleep "$MIN_DWELL"
while :; do
    printf 'screendump %s\n' "$FRAME" | socat - UNIX-CONNECT:"$MONITOR" >/dev/null 2>&1 || true
    if [ -s "$FRAME" ] && python3 "$VISUAL_VALIDATOR" "$FRAME" >"$LAST_CHECK_LOG" 2>&1; then
        FRAME_OK=1
        cat "$LAST_CHECK_LOG"
    fi
    if grep -a -q "dclawos-session" "$SERIAL_LOG" 2>/dev/null; then
        MARKER_OK=1
    fi
    if [ "$FRAME_OK" -eq 1 ] && [ "$MARKER_OK" -eq 1 ]; then
        break
    fi
    [ "$(date +%s)" -ge "$DEADLINE_EPOCH" ] && break
    sleep "$POLL_INTERVAL"
done

set +e
wait "$TIMEOUT_PID"
rc=$?
set -e
cat "$LOG"
dump_guest_console() {
    local why="$1"
    echo "--- guest serial console (${why}) ---"
    if [ ! -s "$SERIAL_LOG" ]; then
        echo "(empty: guest wrote nothing to ttyS0; check the kernel cmdline console= settings)"
        return 0
    fi
    wc -c "$SERIAL_LOG"
    grep -a -iE "dclawos|lightdm|openbox|graphical|FAILED|failed|error" "$SERIAL_LOG" | tail -60 || true
    echo "--- guest serial console tail (last 30 lines) ---"
    tail -30 "$SERIAL_LOG" || true
}
if [ "$FRAME_OK" -ne 1 ] || [ "$MARKER_OK" -ne 1 ]; then
    echo "QEMU $MODE smoke failed: frame_ok=$FRAME_OK marker_ok=$MARKER_OK within ${BUDGET}s budget" >&2
    [ -f "$LAST_CHECK_LOG" ] && cat "$LAST_CHECK_LOG" >&2 || true
    dump_guest_console "session not reached" >&2
    exit 1
fi
if [ "$rc" -ne 124 ]; then
    echo "QEMU $MODE boot smoke failed with exit status $rc" >&2
    dump_guest_console "qemu exited early" >&2
    exit 1
fi
if grep -qiE 'could not open|failed to load|no bootable device|fatal' "$LOG"; then
    echo "QEMU $MODE emitted a boot failure diagnostic" >&2
    dump_guest_console "qemu boot diagnostic" >&2
    exit 1
fi
echo "QEMU $MODE boot smoke passed (frame: $FRAME, session marker found)"
