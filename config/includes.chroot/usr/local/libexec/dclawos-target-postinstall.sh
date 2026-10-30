#!/bin/sh
# DclawOS 2.0 target postinstall. Runs inside the newly installed
# target through Calamares shellprocess (dontChroot: false in
# dclawos-postinstall.conf), NEVER in the live system. Safe to run
# more than once (all removals are guarded or rm -f).
#
# Purpose: enforce the live-vs-installed permission boundary the user
# required — live allows passwordless escalation, the installed system
# must do permission management. This script is the mechanism.
#   live:     dclaw + NOPASSWD sudo + LightDM autologin
#   installed: Calamares-created user + password sudo + greeter login
# Written fresh for 2.0: do NOT copy 1.0's script, which assumed GDM,
# a `user` account, a live-session-init service and an installed
# cleanup helper — none of which exist in 2.0.
set -eu

# 0. Determine the installed account from target /etc/passwd (never
# from installer input: the users module does not reliably expose it).
# Candidates: UID >= 1000, excluding nobody, dclaw-agent (system user)
# and dclaw (the live account). If none remain, the user typed `dclaw`
# as their new username (collision): the installed account IS dclaw
# and must be kept — only the live privileges are stripped.
keep_dclaw=0
installed_account=""
for candidate in $(awk -F: '$3 >= 1000 && $1 != "nobody" \
    && $1 != "dclaw-agent" && $1 != "dclaw" {print $1}' /etc/passwd); do
    installed_account="$candidate"
done
if [ -z "$installed_account" ]; then
    keep_dclaw=1
    installed_account="dclaw"
fi

# 1. Delete the live NOPASSWD rule — unconditionally, collision or not.
# This is the whole key to "installed system does permission
# management"; if it survives, that is a privilege leak, and the
# self-check in step 8 fails the install rather than shipping it.
rm -f /etc/sudoers.d/dclawos-live
# Sweep: any other sudoers.d file granting dclaw NOPASSWD is also a
# live-rule leak (only files under /etc/sudoers.d are touched).
if [ -d /etc/sudoers.d ]; then
    for file in /etc/sudoers.d/*; do
        [ -f "$file" ] || continue
        if grep -q 'dclaw.*NOPASSWD' "$file" 2>/dev/null; then
            rm -f "$file"
        fi
    done
fi

# 2. Delete the live human account, non-collision case only, with home.
if [ "$keep_dclaw" -eq 0 ]; then
    if id dclaw >/dev/null 2>&1; then
        userdel -r dclaw >/dev/null 2>&1 \
            || userdel dclaw >/dev/null 2>&1 || true
    fi
    rm -f /var/lib/AccountsService/users/dclaw
fi

# 3. Installed account gets password sudo via Debian's default %sudo
# rule. No new sudoers file is written here — writing one would only
# add another NOPASSWD leak candidate.
if getent group sudo >/dev/null 2>&1; then
    usermod -aG sudo "$installed_account" >/dev/null 2>&1 || true
fi

# 4. Cancel live autologin (2.0 uses LightDM, not GDM — no gdm3 here).
rm -f /etc/lightdm/lightdm.conf.d/70-dclawos-autologin.conf
if [ -f /etc/lightdm/lightdm.conf ]; then
    sed -i '/^autologin-user=/d' /etc/lightdm/lightdm.conf 2>/dev/null || true
fi

# 5. Clear live session residue (the /var/lib/dclawos directory itself
# stays: group dclawos-session, 0770, the agent/human exchange dir).
rm -f /var/lib/dclawos/xauth
rm -f /var/lib/dclawos/events.jsonl

# 6. Confirm the DclawOS services are enabled in the target (unpackfs
# should already have copied the hook 1004/1006 symlinks; re-create
# if missing). No live-only service exists in 2.0 to disable.
for unit in dclawos-privileged.service dclawos-agent.service; do
    if [ -f "/etc/systemd/system/$unit" ]; then
        mkdir -p /etc/systemd/system/multi-user.target.wants
        ln -sf "/etc/systemd/system/$unit" \
            "/etc/systemd/system/multi-user.target.wants/$unit"
    fi
done

# 7. GRUB / initramfs tail (postinstall runs after bootloader).
command -v update-initramfs >/dev/null 2>&1 \
    && update-initramfs -u -k all || true
command -v update-grub >/dev/null 2>&1 && update-grub || true

# 8. Self-checks: fail the install rather than ship a broken boundary.
if [ -f /etc/sudoers.d/dclawos-live ]; then
    echo 'DclawOS: live sudoers rule survived into target' >&2
    exit 1
fi
if [ "$keep_dclaw" -eq 0 ] && id dclaw >/dev/null 2>&1; then
    echo 'DclawOS: live user survived into target' >&2
    exit 1
fi
if [ -f /etc/lightdm/lightdm.conf.d/70-dclawos-autologin.conf ]; then
    echo 'DclawOS: live autologin survived into target' >&2
    exit 1
fi
if [ "$keep_dclaw" -eq 1 ]; then
    # Collision case: the account is only a valid installed account if
    # the users module actually set a password for it. A locked or
    # password-less dclaw here means the users step failed — fail
    # instead of installing a system nobody can log into.
    dclaw_status=$(passwd -S dclaw 2>/dev/null | awk '{print $2}')
    if [ "$dclaw_status" != "P" ]; then
        echo "DclawOS: collision account dclaw has no usable password (status: $dclaw_status)" >&2
        exit 1
    fi
fi
echo "DclawOS: target postinstall done (installed account: $installed_account)"
