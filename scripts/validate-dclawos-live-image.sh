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
unsquashfs -d "$WORK/release-extract" "$FS" usr/share/dclawos/release >/dev/null 2>&1
grep -q 'DclawOS 2.0' "$WORK/release-extract/usr/share/dclawos/release"

# Initramfs must contain live-boot (the live session depends on it).
lsinitramfs "$INITRD" | grep -q 'scripts/live' || {
    echo "initramfs lacks live-boot scripts" >&2
    exit 1
}

echo "final ISO validation passed: $ISO"
