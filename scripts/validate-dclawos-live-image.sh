#!/usr/bin/env bash
# Verify the finished DclawOS 2.0 ISO, not merely the source configuration.
# Read-only: extracts selected files from the final squashfs and checks
# Phase-1 session-critical paths, plus negative scope checks proving the
# reboot direction (no full GNOME desktop, no Firefox).
set -euo pipefail

validation_error() {
    status="$?"
    echo "final ISO validation failed near line $1" >&2
    exit "$status"
}
trap 'validation_error $LINENO' ERR

[ "$(id -u)" = 0 ] || {
    echo 'run final ISO validation as root (sudo) for full squashfs integrity checking' >&2
    exit 2
}

[ "$#" -eq 1 ] || {
    echo "usage: $0 DclawOS-2.0-amd64-livecd.iso" >&2
    exit 2
}

ISO="$1"
[ -s "$ISO" ] || {
    echo "ISO is missing or empty: $ISO" >&2
    exit 1
}
for command in xorriso unsquashfs lsinitramfs grep python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "required command is unavailable: $command" >&2
        exit 1
    }
done

WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
FS="$WORK/filesystem.squashfs"
LIST="$WORK/squashfs.list"

INITRD="$WORK/initrd.img"
xorriso -osirrox on -indev "$ISO" \
    -extract /live/filesystem.squashfs "$FS" \
    -extract /live/initrd.img "$INITRD" >/dev/null 2>&1

unsquashfs -l "$FS" > "$LIST"
# Listing format note (run 4 lesson): unsquashfs -l prefixes entries
# (e.g. squashfs-root/...), so exact full-line matches miss. Match the
# path suffix anchored at a boundary instead; this covers slash-less,
# slash-prefixed and squashfs-root-prefixed listings alike.

# Phase-1 session-critical paths must be present in the final image.
for path in \
    usr/local/bin/dclawos-shell \
    usr/local/bin/dclawos-dashboard \
    usr/local/bin/dclawos-log-event \
    usr/local/bin/dclawos-settings \
    usr/local/bin/dclawos-route \
    usr/local/bin/dclawos-privileged \
    usr/local/bin/dclawos-ticket \
    usr/local/lib/dclawos/ticketlib.py \
    etc/systemd/system/dclawos-privileged.service \
    etc/systemd/system/multi-user.target.wants/dclawos-privileged.service \
    usr/share/glib-2.0/schemas/org.dclawos.settings.gschema.xml \
    usr/share/glib-2.0/schemas/gschemas.compiled \
    usr/share/dclawos/engine/decision.js \
    usr/share/dclawos/engine/catalog.json \
    usr/share/dclawos/engine-src/README.md \
    opt/dclawos-node/bin/node \
    var/lib/dclawos \
    etc/lightdm/lightdm.conf.d/70-dclawos-autologin.conf \
    etc/xdg/openbox/autostart \
    usr/share/dclawos/release \
    usr/bin/openbox \
    usr/sbin/lightdm \
    usr/bin/python3 \
; do
    grep -qE "(^|/)${path}$" "$LIST" || {
        echo "missing from final image: /$path" >&2
        exit 1
    }
done

# Negative scope checks: the reboot ships no full GNOME desktop and no
# Firefox. If either binary reappears, the direction regressed.
for path in \
    usr/bin/gnome-shell \
    usr/bin/firefox \
; do
    if grep -qE "(^|/)${path}$" "$LIST"; then
        echo "scope regression: forbidden binary present in final image: /$path" >&2
        exit 1
    fi
done

# The shell script inside the image must be executable and syntactically
# valid Python (checked on the extracted file, not the repo copy).
SHELL_EXTRACT="$WORK/dclawos-shell"
unsquashfs -d "$WORK/shell-extract" "$FS" usr/local/bin/dclawos-shell >/dev/null 2>&1
cp "$WORK/shell-extract/usr/local/bin/dclawos-shell" "$SHELL_EXTRACT"
test -x "$SHELL_EXTRACT"
python3 -m py_compile "$SHELL_EXTRACT"
# Phase 2 module 1 wiring presence check. This is NOT a behavior test:
# Wnck switching needs a live X session, which this host-side gate cannot
# provide. It only proves the switching symbols survived into the final
# image (a revert to the Phase 1 logging stub would fail here).
grep -q 'activate_window' "$SHELL_EXTRACT" || {
    echo "taskbar switching wiring missing from final image" >&2
    exit 1
}
grep -q 'Wnck' "$SHELL_EXTRACT" || {
    echo "Wnck import missing from final image shell" >&2
    exit 1
}
# Phase 2 module 2: dashboard + event writer must be executable and
# syntactically valid Python (same rule as the shell above).
for tool in dclawos-dashboard dclawos-log-event; do
    unsquashfs -d "$WORK/${tool}-extract" "$FS" "usr/local/bin/${tool}" >/dev/null 2>&1
    test -x "$WORK/${tool}-extract/usr/local/bin/${tool}"
    python3 -m py_compile "$WORK/${tool}-extract/usr/local/bin/${tool}"
done
# The dashboard must be autostarted: it IS the default desktop view.
unsquashfs -d "$WORK/autostart-extract" "$FS" etc/xdg/openbox/autostart >/dev/null 2>&1
grep -q 'dclawos-dashboard' "$WORK/autostart-extract/etc/xdg/openbox/autostart" || {
    echo "dashboard missing from session autostart" >&2
    exit 1
}
# Phase 2 module 3: settings app must be executable + valid Python.
unsquashfs -d "$WORK/settings-extract" "$FS" usr/local/bin/dclawos-settings >/dev/null 2>&1
test -x "$WORK/settings-extract/usr/local/bin/dclawos-settings"
python3 -m py_compile "$WORK/settings-extract/usr/local/bin/dclawos-settings"
# Phase 2 module 4: routing engine bundle must exist, expose the
# decision API, and ship a valid catalog (presence + API + JSON gates;
# the build-time hook already ran the real behavior selftest inside
# the chroot, which the host-side gate cannot reproduce without X).
unsquashfs -d "$WORK/engine-extract" "$FS" usr/share/dclawos/engine >/dev/null 2>&1
test -s "$WORK/engine-extract/usr/share/dclawos/engine/decision.js"
grep -q 'decideRouting' "$WORK/engine-extract/usr/share/dclawos/engine/decision.js" || {
    echo "engine bundle lacks decideRouting API" >&2
    exit 1
}
python3 -m json.tool "$WORK/engine-extract/usr/share/dclawos/engine/catalog.json" > /dev/null || {
    echo "engine catalog.json invalid" >&2
    exit 1
}
unsquashfs -d "$WORK/route-extract" "$FS" usr/local/bin/dclawos-route >/dev/null 2>&1
test -x "$WORK/route-extract/usr/local/bin/dclawos-route"
grep -q 'decision.js' "$WORK/route-extract/usr/local/bin/dclawos-route" || {
    echo "dclawos-route does not reference the engine bundle" >&2
    exit 1
}
# Phase 2 module 5: privilege broker + ticket CLI + shared ticket lib
# must be executable and valid Python; the unit must start the broker
# and be wanted by multi-user.target (hook 1004 enables it).
for tool in dclawos-privileged dclawos-ticket; do
    unsquashfs -d "$WORK/${tool}-extract" "$FS" "usr/local/bin/${tool}" >/dev/null 2>&1
    test -x "$WORK/${tool}-extract/usr/local/bin/${tool}"
    python3 -m py_compile "$WORK/${tool}-extract/usr/local/bin/${tool}"
done
unsquashfs -d "$WORK/ticketlib-extract" "$FS" usr/local/lib/dclawos/ticketlib.py >/dev/null 2>&1
python3 -m py_compile "$WORK/ticketlib-extract/usr/local/lib/dclawos/ticketlib.py"
unsquashfs -d "$WORK/privunit-extract" "$FS" etc/systemd/system/dclawos-privileged.service >/dev/null 2>&1
grep -q 'ExecStart=/usr/local/bin/dclawos-privileged' "$WORK/privunit-extract/etc/systemd/system/dclawos-privileged.service" || {
    echo "privilege broker unit has wrong ExecStart" >&2
    exit 1
}
grep -q 'WantedBy=multi-user.target' "$WORK/privunit-extract/etc/systemd/system/dclawos-privileged.service" || {
    echo "privilege broker unit missing WantedBy=multi-user.target" >&2
    exit 1
}
unsquashfs -d "$WORK/release-extract" "$FS" usr/share/dclawos/release >/dev/null 2>&1
grep -q 'DclawOS 2.0' "$WORK/release-extract/usr/share/dclawos/release"

# Initramfs must contain live-boot (the live session depends on it).
lsinitramfs "$INITRD" | grep -q 'scripts/live' || {
    echo "initramfs lacks live-boot scripts" >&2
    exit 1
}

echo "final ISO validation passed: $ISO"
