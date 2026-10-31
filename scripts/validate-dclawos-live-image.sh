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
    usr/local/bin/dclawos-llm \
    usr/local/bin/dclawos-privileged \
    usr/local/bin/dclawos-ticket \
    usr/local/bin/dclawos-takeover \
    usr/local/bin/dclawos-takeover-request \
    usr/local/bin/dclawos-gui \
    usr/local/bin/dclawos-agent-ctl \
    usr/local/bin/dclawos-agent \
    usr/local/bin/dclawos-approve \
    usr/local/bin/dclawos-secret \
    usr/local/bin/dclawos-chat \
    usr/local/bin/dclawos-connector-local \
    usr/local/lib/dclawos/agentlib.py \
    usr/local/lib/dclawos/mcpclient.py \
    usr/local/lib/dclawos/skills.py \
    usr/local/lib/dclawos/connectorlib.py \
    etc/systemd/system/dclawos-agent.service \
    etc/systemd/system/multi-user.target.wants/dclawos-agent.service \
    usr/local/bin/dclawos-monitor \
    usr/local/bin/dclawos-agent-run \
    usr/local/bin/dclawos-editor \
    usr/bin/calamares \
    etc/calamares/settings.conf \
    usr/local/bin/dclawos-installer \
    usr/share/applications/dclawos-installer.desktop \
    etc/calamares/modules/dclawos-postinstall.conf \
    usr/local/libexec/dclawos-target-postinstall.sh \
    etc/calamares/branding/dclawos/branding.desc \
    etc/calamares/branding/dclawos/show.qml \
    etc/calamares/branding/dclawos/stylesheet.qss \
    etc/calamares/branding/dclawos/dclawos-mark.svg \
    usr/local/lib/dclawos/modifiedfiles.py \
    usr/local/lib/dclawos/registrylib.py \
    usr/lib/x86_64-linux-gnu/girepository-1.0/GtkSource-4.typelib \
    usr/local/lib/dclawos/ticketlib.py \
    usr/local/lib/dclawos/takeoverlib.py \
    usr/bin/xdotool \
    usr/bin/chromium \
    usr/share/applications/chromium.desktop \
    etc/systemd/system/dclawos-privileged.service \
    etc/systemd/system/multi-user.target.wants/dclawos-privileged.service \
    usr/share/glib-2.0/schemas/org.dclawos.settings.gschema.xml \
    usr/share/glib-2.0/schemas/gschemas.compiled \
    usr/share/dclawos/engine/decision.js \
    usr/share/dclawos/engine/llm.js \
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
# Phase 2 module 10: Chromium must occupy a taskbar slot (Q5).
grep -q '("Chromium", "chromium", "Chromium")' "$SHELL_EXTRACT" || {
    echo "taskbar lacks the Chromium slot" >&2
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
# Phase 2 module 9: settings must wire the unified skills/MCP registry.
grep -q 'registrylib' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings lacks the unified registry wiring" >&2
    exit 1
}
grep -q 'mcp_servers' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings lacks the MCP registry wiring" >&2
    exit 1
}
unsquashfs -d "$WORK/registrylib-extract" "$FS" usr/local/lib/dclawos/registrylib.py >/dev/null 2>&1
python3 -m py_compile "$WORK/registrylib-extract/usr/local/lib/dclawos/registrylib.py"
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
# Phase 4 module 1: LLM execution bundle + CLI must exist; the llm
# bundle carries the executor marker, and decision.js must still be
# free of the dispatch marker (decision stays a pure offline bundle;
# the build hook already ran the mock-endpoint behavior selftest).
test -s "$WORK/engine-extract/usr/share/dclawos/engine/llm.js"
grep -q 'dclawos-llm-executor-v1' "$WORK/engine-extract/usr/share/dclawos/engine/llm.js" || {
    echo "llm bundle lacks executor marker" >&2
    exit 1
}
grep -q 'dclawos-dispatch' "$WORK/engine-extract/usr/share/dclawos/engine/decision.js" && {
    echo "decision bundle contaminated with dispatch code" >&2
    exit 1
}
unsquashfs -d "$WORK/llm-extract" "$FS" usr/local/bin/dclawos-llm >/dev/null 2>&1
test -x "$WORK/llm-extract/usr/local/bin/dclawos-llm"
grep -q 'llm.js' "$WORK/llm-extract/usr/local/bin/dclawos-llm" || {
    echo "dclawos-llm does not reference the llm bundle" >&2
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
# Phase 3 module 1: agent service daemon + approval popup + secret CLI
# + shared agent lib + agent unit (enabled by hook 1006). The daemon
# itself needs a live session to run tasks, so the gate checks
# presence + syntax + unit wiring, not behavior.
for tool in dclawos-agent dclawos-approve dclawos-secret dclawos-chat \
    dclawos-connector-local; do
    unsquashfs -d "$WORK/${tool}-extract" "$FS" "usr/local/bin/${tool}" >/dev/null 2>&1
    test -x "$WORK/${tool}-extract/usr/local/bin/${tool}"
    python3 -m py_compile "$WORK/${tool}-extract/usr/local/bin/${tool}"
done
unsquashfs -d "$WORK/agentlib-extract" "$FS" usr/local/lib/dclawos/agentlib.py >/dev/null 2>&1
python3 -m py_compile "$WORK/agentlib-extract/usr/local/lib/dclawos/agentlib.py"
for lib in mcpclient skills connectorlib convlib toolslib llmlib; do
    unsquashfs -d "$WORK/${lib}-extract" "$FS" "usr/local/lib/dclawos/${lib}.py" >/dev/null 2>&1
    python3 -m py_compile "$WORK/${lib}-extract/usr/local/lib/dclawos/${lib}.py"
done
# Phase 4 module 2: the agent daemon must carry the LLM loop and the
# chat window must create llm tasks (behavior itself is gated at
# build time by hook 1008 against a mock endpoint).
grep -q 'run_llm_task' "$WORK/dclawos-agent-extract/usr/local/bin/dclawos-agent" || {
    echo "agent daemon lacks the LLM loop" >&2
    exit 1
}
grep -q 'kind="llm"' "$WORK/dclawos-chat-extract/usr/local/bin/dclawos-chat" || {
    echo "chat window does not create llm tasks" >&2
    exit 1
}
grep -q 'classify' "$WORK/agentlib-extract/usr/local/lib/dclawos/agentlib.py" || {
    echo "agent lib lacks approval classification" >&2
    exit 1
}
grep -q 'connector_send' "$WORK/agentlib-extract/usr/local/lib/dclawos/agentlib.py" || {
    echo "agent lib lacks connector_send tool" >&2
    exit 1
}
# Phase 3 module 4: full-access toggle. The schema must carry the key
# and the settings GUI must wire it; the service itself never writes
# its own permission. (P5-M7: the value now lives in the shared
# settings file — settingslib — which agentlib and the GUI both use;
# the schema remains as the seed source / key inventory.)
# (settings already extracted to $WORK/settings-extract by the
# Phase 2 block above; reuse it, do not re-extract.)
unsquashfs -d "$WORK/schema-extract" "$FS" usr/share/glib-2.0/schemas/org.dclawos.settings.gschema.xml >/dev/null 2>&1
grep -q 'agent-full-access' "$WORK/schema-extract/usr/share/glib-2.0/schemas/org.dclawos.settings.gschema.xml" || {
    echo "schema lacks agent-full-access key" >&2
    exit 1
}
grep -q 'agent-full-access' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings does not wire agent-full-access" >&2
    exit 1
}
# Phase 5 follow-up (P5-M7): shared settings store. The lib must be
# packaged and valid, carry the denylist and the shared path, the
# settings GUI must write through it, and the agent lib must expose
# the settings tools (behavior is gated in-chroot by hook 1008's
# settingstest; these are the static presence checks).
unsquashfs -d "$WORK/settingslib-extract" "$FS" usr/local/lib/dclawos/settingslib.py usr/local/lib/dclawos/settingstest.py >/dev/null 2>&1
python3 -m py_compile "$WORK/settingslib-extract/usr/local/lib/dclawos/settingslib.py" "$WORK/settingslib-extract/usr/local/lib/dclawos/settingstest.py"
grep -q 'AGENT_DENIED_KEYS' "$WORK/settingslib-extract/usr/local/lib/dclawos/settingslib.py" || {
    echo "settingslib lacks the agent denylist" >&2
    exit 1
}
grep -q 'settings/agent-settings.json' "$WORK/settingslib-extract/usr/local/lib/dclawos/settingslib.py" || {
    echo "settingslib lacks the hardened shared settings path" >&2
    exit 1
}
# (broker already extracted to $WORK/dclawos-privileged-extract by
# the Phase 2 module 5 block above; reuse it.)
grep -q 'set-setting' "$WORK/dclawos-privileged-extract/usr/local/bin/dclawos-privileged" || {
    echo "privilege broker lacks the set-setting operation" >&2
    exit 1
}
grep -q 'agent_denied' "$WORK/dclawos-privileged-extract/usr/local/bin/dclawos-privileged" || {
    echo "privilege broker does not enforce the settings denylist" >&2
    exit 1
}
grep -q 'settingslib' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings GUI does not write through settingslib" >&2
    exit 1
}
grep -q 'settings_set' "$WORK/agentlib-extract/usr/local/lib/dclawos/agentlib.py" || {
    echo "agent lib lacks the settings_set tool" >&2
    exit 1
}
# Phase 3 module 2: chat summon target + settings connectors/secrets
# pages. The hook retargets menu + keybind to dclawos-chat; the gate
# reads the packaged Openbox configs from the image.
unsquashfs -d "$WORK/menu-extract" "$FS" etc/xdg/openbox/menu.xml >/dev/null 2>&1
grep -q 'dclawos-chat' "$WORK/menu-extract/etc/xdg/openbox/menu.xml" || {
    echo "desktop menu does not summon the chat window" >&2
    exit 1
}
# (settings already extracted to $WORK/settings-extract by the
# Phase 2 block above; re-extracting to the same directory fails
# because unsquashfs refuses to overwrite existing files. Reuse it.)
grep -q 'connectors' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings lacks connectors management" >&2
    exit 1
}
grep -q 'build_secrets_panel' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings lacks secrets page" >&2
    exit 1
}
# Phase 3 module 5: livecd installer (Calamares). Static checks only:
# they prove the installer is packaged + wired + the live/installed
# permission boundary is scripted — they do NOT claim an install
# succeeds (that needs the QEMU install test, a follow-up).
unsquashfs -d "$WORK/installer-extract" "$FS" usr/local/bin/dclawos-installer >/dev/null 2>&1
sh -n "$WORK/installer-extract/usr/local/bin/dclawos-installer"
grep -q 'sudo -n -E' "$WORK/installer-extract/usr/local/bin/dclawos-installer" || {
    echo "installer wrapper must use sudo -n (no polkit agent on bare Openbox)" >&2
    exit 1
}
if grep -v '^[[:space:]]*#' "$WORK/installer-extract/usr/local/bin/dclawos-installer" | grep -q 'pkexec'; then
    echo "installer wrapper must not invoke pkexec" >&2
    exit 1
fi
if grep -v '^[[:space:]]*#' "$WORK/installer-extract/usr/local/bin/dclawos-installer" | grep -q '/home/user'; then
    echo "installer wrapper still references 1.0 /home/user paths" >&2
    exit 1
fi
grep -q 'dclawos-installer' "$WORK/menu-extract/etc/xdg/openbox/menu.xml" || {
    echo "desktop menu lacks the installer entry" >&2
    exit 1
}
unsquashfs -d "$WORK/calamares-extract" "$FS" etc/calamares/settings.conf etc/calamares/modules/dclawos-postinstall.conf usr/local/libexec/dclawos-target-postinstall.sh >/dev/null 2>&1
grep -qE '^[[:space:]]*branding:[[:space:]]*dclawos[[:space:]]*$' "$WORK/calamares-extract/etc/calamares/settings.conf" || {
    echo "calamares branding is not dclawos" >&2
    exit 1
}
grep -q 'dontChroot: false' "$WORK/calamares-extract/etc/calamares/modules/dclawos-postinstall.conf" || {
    echo "postinstall must run in the target chroot (dontChroot: false)" >&2
    exit 1
}
# Exec-sequence order, line-based (no PyYAML in the image): the
# postinstall must sit after users and after bootloader, before
# umount when umount is present; packages must be gone (offline).
awk '
    /^[[:space:]]*-[[:space:]]*exec:[[:space:]]*$/ { in_exec=1; next }
    in_exec && /^[[:space:]]*-[[:space:]]*[a-z]+:/ { in_exec=0 }
    in_exec {
        if ($0 ~ /^[[:space:]]*-[[:space:]]*users[[:space:]]*$/) users=NR
        if ($0 ~ /^[[:space:]]*-[[:space:]]*bootloader[[:space:]]*$/) bootloader=NR
        if ($0 ~ /shellprocess@dclawos-postinstall/) postinstall=NR
        if ($0 ~ /^[[:space:]]*-[[:space:]]*umount[[:space:]]*$/) umount=NR
        if ($0 ~ /^[[:space:]]*-[[:space:]]*packages[[:space:]]*$/) packages=NR
    }
    END {
        if (!users || !postinstall) exit 2
        if (postinstall < users) exit 3
        if (bootloader && postinstall < bootloader) exit 4
        if (umount && postinstall > umount) exit 5
        if (packages) exit 6
    }
' "$WORK/calamares-extract/etc/calamares/settings.conf" || {
    echo "calamares exec sequence order wrong (users < postinstall, bootloader < postinstall < umount, no packages)" >&2
    exit 1
}
grep -q 'id: dclawos-postinstall' "$WORK/calamares-extract/etc/calamares/settings.conf" || {
    echo "calamares instances lack dclawos-postinstall" >&2
    exit 1
}
# Permission boundary, static double proof: the live rule must be
# present in the image, and the postinstall must delete it + the
# autologin file, without touching GDM (2.0 has none) or dclaw-agent.
unsquashfs -d "$WORK/livesudo-extract" "$FS" etc/sudoers.d >/dev/null 2>&1
grep -q 'dclaw ALL=(ALL) NOPASSWD: ALL' "$WORK/livesudo-extract/etc/sudoers.d/dclawos-live" || {
    echo "live image lacks the NOPASSWD rule (live must escalate password-free)" >&2
    exit 1
}
grep -q 'rm -f /etc/sudoers.d/dclawos-live' "$WORK/calamares-extract/usr/local/libexec/dclawos-target-postinstall.sh" || {
    echo "postinstall does not delete the live sudoers rule" >&2
    exit 1
}
grep -q '70-dclawos-autologin.conf' "$WORK/calamares-extract/usr/local/libexec/dclawos-target-postinstall.sh" || {
    echo "postinstall does not cancel live autologin" >&2
    exit 1
}
if grep -v '^[[:space:]]*#' "$WORK/calamares-extract/usr/local/libexec/dclawos-target-postinstall.sh" | grep -q 'gdm3'; then
    echo "postinstall must not reference gdm3 (2.0 uses LightDM)" >&2
    exit 1
fi
if grep -v '^[[:space:]]*#' "$WORK/calamares-extract/usr/local/libexec/dclawos-target-postinstall.sh" | grep -q 'userdel.*dclaw-agent'; then
    echo "postinstall must not delete dclaw-agent" >&2
    exit 1
fi
sh -n "$WORK/calamares-extract/usr/local/libexec/dclawos-target-postinstall.sh"
# Toolchain presence in the image (offline bootloader + unpackfs).
for toolpath in usr/bin/unsquashfs usr/bin/rsync usr/sbin/parted; do
    grep -q "$toolpath" "$LIST" || {
        echo "installer toolchain missing from image: $toolpath" >&2
        exit 1
    }
done
grep -q 'calamares/modules/partition/libcalamares_viewmodule_partition.so' "$LIST" || {
    echo "calamares partition module missing from image" >&2
    exit 1
}
grep -q 'calamares/modules/mount/module.desc' "$LIST" || {
    echo "calamares mount module missing from image" >&2
    exit 1
}
grep -q 'grub/i386-pc/modinfo.sh' "$LIST" || {
    echo "GRUB BIOS platform modules missing from image" >&2
    exit 1
}
grep -q 'grub/x86_64-efi/modinfo.sh' "$LIST" || {
    echo "GRUB EFI platform modules missing from image" >&2
    exit 1
}
unsquashfs -d "$WORK/agentunit-extract" "$FS" etc/systemd/system/dclawos-agent.service >/dev/null 2>&1
grep -q 'ExecStart=/usr/local/bin/dclawos-agent' "$WORK/agentunit-extract/etc/systemd/system/dclawos-agent.service" || {
    echo "agent service unit has wrong ExecStart" >&2
    exit 1
}
grep -q 'User=dclaw-agent' "$WORK/agentunit-extract/etc/systemd/system/dclawos-agent.service" || {
    echo "agent service unit must run as dclaw-agent" >&2
    exit 1
}
# Phase 2 module 6: takeover popup + request CLI + GUI op CLI + shared
# takeover lib must be executable and valid Python; the lib must carry
# the credential-key filter (the Q2 backfill guarantee).
for tool in dclawos-takeover dclawos-takeover-request dclawos-gui; do
    unsquashfs -d "$WORK/${tool}-extract" "$FS" "usr/local/bin/${tool}" >/dev/null 2>&1
    test -x "$WORK/${tool}-extract/usr/local/bin/${tool}"
    python3 -m py_compile "$WORK/${tool}-extract/usr/local/bin/${tool}"
done
unsquashfs -d "$WORK/takeoverlib-extract" "$FS" usr/local/lib/dclawos/takeoverlib.py >/dev/null 2>&1
python3 -m py_compile "$WORK/takeoverlib-extract/usr/local/lib/dclawos/takeoverlib.py"
grep -q 'FORBIDDEN_RESULT_KEYS' "$WORK/takeoverlib-extract/usr/local/lib/dclawos/takeoverlib.py" || {
    echo "takeover lib lacks the credential-key filter" >&2
    exit 1
}
# Dashboard must carry the takeover banner (module 6 launch path).
# Phase 6 M4 supersedes the location: the banner is now an element
# of the dashboard WebView page, fed by dashboardlib's pending
# takeover views (the pre-M4 GTK banner widget is gone with the
# GTK timeline); the launch path is dashboardlib's launch table.
unsquashfs -d "$WORK/dash-takeover-extract" "$FS" usr/local/bin/dclawos-dashboard >/dev/null 2>&1
python3 -m py_compile "$WORK/dash-takeover-extract/usr/local/bin/dclawos-dashboard"
unsquashfs -d "$WORK/dashlib-extract" "$FS" usr/local/lib/dclawos/dashboardlib.py usr/share/dclawos/dashboard.html >/dev/null 2>&1
python3 -m py_compile "$WORK/dashlib-extract/usr/local/lib/dclawos/dashboardlib.py"
grep -q 'takeoverbar' "$WORK/dashlib-extract/usr/share/dclawos/dashboard.html" || {
    echo "dashboard page lacks the takeover banner" >&2
    exit 1
}
grep -q 'pending_takeover_views' "$WORK/dashlib-extract/usr/local/lib/dclawos/dashboardlib.py" || {
    echo "dashboardlib lacks the pending takeover views" >&2
    exit 1
}
# Phase 2 module 7: separation + monitoring. The tools must be valid;
# the broker must use the shared exchange group; and the separation
# itself is checked against the image's passwd/group/home modes.
for tool in dclawos-agent-ctl dclawos-monitor; do
    unsquashfs -d "$WORK/${tool}-extract" "$FS" "usr/local/bin/${tool}" >/dev/null 2>&1
    test -x "$WORK/${tool}-extract/usr/local/bin/${tool}"
    python3 -m py_compile "$WORK/${tool}-extract/usr/local/bin/${tool}"
done
unsquashfs -d "$WORK/agent-run-extract" "$FS" usr/local/bin/dclawos-agent-run >/dev/null 2>&1
test -x "$WORK/agent-run-extract/usr/local/bin/dclawos-agent-run"
sh -n "$WORK/agent-run-extract/usr/local/bin/dclawos-agent-run"
grep -q 'dclawos-monitor' "$WORK/dashlib-extract/usr/local/lib/dclawos/dashboardlib.py" || {
    echo "dashboard lacks the monitor launch target (M4: dashboardlib)" >&2
    exit 1
}
unsquashfs -d "$WORK/privbroker-extract" "$FS" usr/local/bin/dclawos-privileged >/dev/null 2>&1
grep -q 'dclawos-session' "$WORK/privbroker-extract/usr/local/bin/dclawos-privileged" || {
    echo "broker lacks the shared exchange group" >&2
    exit 1
}
unsquashfs -d "$WORK/passwd-extract" "$FS" etc/passwd >/dev/null 2>&1
grep -q '^dclaw-agent:' "$WORK/passwd-extract/etc/passwd" || {
    echo "dclaw-agent user missing from final image" >&2
    exit 1
}
unsquashfs -d "$WORK/group-extract" "$FS" etc/group >/dev/null 2>&1
GROUP_LINE=$(grep '^dclawos-session:' "$WORK/group-extract/etc/group" || true)
[ -n "$GROUP_LINE" ] || {
    echo "dclawos-session group missing" >&2
    exit 1
}
# Members field wrapped in commas so ,dclaw, cannot match dclaw-agent.
MEMBERS=",${GROUP_LINE##*:},"
case "$MEMBERS" in
    *,dclaw,*) ;;
    *)
        echo "dclaw missing from dclawos-session" >&2
        exit 1
        ;;
esac
case "$MEMBERS" in
    *,dclaw-agent,*) ;;
    *)
        echo "dclaw-agent missing from dclawos-session" >&2
        exit 1
        ;;
esac
unsquashfs -d "$WORK/sudoers-extract" "$FS" etc/sudoers.d >/dev/null 2>&1
[ -d "$WORK/sudoers-extract/etc/sudoers.d" ] || {
    echo "sudoers.d missing from final image" >&2
    exit 1
}
if grep -rq 'dclaw-agent' "$WORK/sudoers-extract/etc/sudoers.d" 2>/dev/null; then
    echo "dclaw-agent must not appear in sudoers" >&2
    exit 1
fi
unsquashfs -d "$WORK/home-extract" "$FS" home/dclaw home/dclaw-agent >/dev/null 2>&1
for home in dclaw dclaw-agent; do
    [ -d "$WORK/home-extract/home/${home}" ] || {
        echo "home dir missing from final image: /home/${home}" >&2
        exit 1
    }
    mode=$(stat -c '%a' "$WORK/home-extract/home/${home}")
    [ "$mode" = "750" ] || {
        echo "home dir not 0750: /home/${home} is ${mode}" >&2
        exit 1
    }
done
# Phase 2 module 8: editor + modified-files lib must be valid Python;
# the editor must use the native GtkSourceView (not a web view, not an
# embedded terminal) and the dashboard must launch it.
unsquashfs -d "$WORK/editor-extract" "$FS" usr/local/bin/dclawos-editor >/dev/null 2>&1
test -x "$WORK/editor-extract/usr/local/bin/dclawos-editor"
python3 -m py_compile "$WORK/editor-extract/usr/local/bin/dclawos-editor"
grep -q 'GtkSource' "$WORK/editor-extract/usr/local/bin/dclawos-editor" || {
    echo "editor does not use GtkSourceView" >&2
    exit 1
}
# No embedded terminal / web view: match import patterns, not prose
# mentions (the editor docstring discusses the ban in words).
if grep -qE 'require_version\("?(Vte|WebKit)|from gi.repository import.*(Vte|WebKit)|import (Vte|WebKit)' "$WORK/editor-extract/usr/local/bin/dclawos-editor"; then
    echo "editor embeds a terminal or web view (forbidden)" >&2
    exit 1
fi
unsquashfs -d "$WORK/modifiedfiles-extract" "$FS" usr/local/lib/dclawos/modifiedfiles.py >/dev/null 2>&1
python3 -m py_compile "$WORK/modifiedfiles-extract/usr/local/lib/dclawos/modifiedfiles.py"
grep -q 'dclawos-editor' "$WORK/dashlib-extract/usr/local/lib/dclawos/dashboardlib.py" || {
    echo "dashboard lacks the editor launch target (M4: dashboardlib)" >&2
    exit 1
}
unsquashfs -d "$WORK/release-extract" "$FS" usr/share/dclawos/release >/dev/null 2>&1
grep -q 'DclawOS 2.0' "$WORK/release-extract/usr/share/dclawos/release"

# Phase 3 module 7: shared GUI theme + headerbars (look-only pass).
# theme.py + theme.css must be present; every main window app must
# call apply_theme() and carry a HeaderBar. CSS brace balance is
# checked because a broken stylesheet would silently fail to load.
# POLICY CHANGE (Phase 5, user directive 2026-10-31): the taskbar
# IS themed now — palette/typography only, via the .dclawos-taskbar
# class scope in theme.css; its layout geometry stays as Phase 3
# froze it. The old "taskbar must not be themed" assertion below
# was replaced by its positive form; see docs/UI-RULES.md.
unsquashfs -d "$WORK/theme-extract" "$FS" usr/local/lib/dclawos/theme.py usr/share/dclawos/theme.css >/dev/null 2>&1
test -f "$WORK/theme-extract/usr/local/lib/dclawos/theme.py"
test -f "$WORK/theme-extract/usr/share/dclawos/theme.css"
python3 -m py_compile "$WORK/theme-extract/usr/local/lib/dclawos/theme.py"
python3 - "$WORK/theme-extract/usr/share/dclawos/theme.css" <<'PYEOF'
import sys
css = open(sys.argv[1]).read()
assert css.count("{") == css.count("}"), "theme.css braces unbalanced"
assert "headerbar" in css, "theme.css lacks headerbar styling"
PYEOF
for app in dclawos-dashboard dclawos-settings dclawos-editor dclawos-chat dclawos-approve dclawos-monitor dclawos-takeover; do
    case "$app" in
        dclawos-dashboard) src="$WORK/dash-takeover-extract/usr/local/bin/dclawos-dashboard" ;;
        dclawos-settings) src="$WORK/settings-extract/usr/local/bin/dclawos-settings" ;;
        dclawos-editor) src="$WORK/editor-extract/usr/local/bin/dclawos-editor" ;;
        *) unsquashfs -d "$WORK/theme-app-$app" "$FS" "usr/local/bin/$app" >/dev/null 2>&1
           src="$WORK/theme-app-$app/usr/local/bin/$app" ;;
    esac
    grep -q 'import theme' "$src" || { echo "$app lacks theme import" >&2; exit 1; }
    grep -q 'theme.apply_theme()' "$src" || { echo "$app does not call apply_theme" >&2; exit 1; }
    grep -q 'Gtk.HeaderBar' "$src" || { echo "$app lacks a HeaderBar" >&2; exit 1; }
done
unsquashfs -d "$WORK/theme-shell" "$FS" usr/local/bin/dclawos-shell >/dev/null 2>&1
grep -q 'apply_theme' "$WORK/theme-shell/usr/local/bin/dclawos-shell" || {
    echo "taskbar lost apply_theme (Phase 5 palette alignment)" >&2
    exit 1
}
grep -q '\.dclawos-taskbar' "$WORK/theme-extract/usr/share/dclawos/theme.css" || {
    echo "theme.css lost the .dclawos-taskbar scope" >&2
    exit 1
}

# Phase 5 module 5: the 10 iron rules land in the theme system
# (docs/UI-RULES.md). Gate-level proxies: binding palette literals
# present, NO pure black #000 in either stylesheet (comments
# stripped first — the rule text itself mentions #000), dark
# variant shipped, theme.py carries the code-level helpers
# (stream reveal, feedback fade, revealer sections, tnum, prose),
# the schema carries ui-dark, chat applies the reveal + copy
# feedback, and settings exposes the dark toggle.
unsquashfs -d "$WORK/theme-dark-extract" "$FS" usr/share/dclawos/theme-dark.css >/dev/null 2>&1
test -f "$WORK/theme-dark-extract/usr/share/dclawos/theme-dark.css"
python3 - "$WORK/theme-extract/usr/share/dclawos/theme.css" "$WORK/theme-dark-extract/usr/share/dclawos/theme-dark.css" <<'PYEOF'
import re
import sys
light = open(sys.argv[1], encoding="utf-8").read()
dark = open(sys.argv[2], encoding="utf-8").read()
strip = lambda css: re.sub(r"/\*.*?\*/", "", css, flags=re.S)
assert "#000" not in strip(light), "theme.css uses pure black"
assert "#000" not in strip(dark), "theme-dark.css uses pure black"
for token in ("#f5f5f3", "#fcfcfb"):
    assert token in light, f"theme.css lost palette layer {token}"
for token in ("#0b0b0a", "#151515", "#1a1a19", "#20201e"):
    assert token in dark, f"theme-dark.css lost palette layer {token}"
PYEOF
for sym in stream_reveal flash_feedback make_section apply_tnum apply_prose; do
    grep -q "def $sym" "$WORK/theme-extract/usr/local/lib/dclawos/theme.py" || {
        echo "theme.py lost helper $sym" >&2
        exit 1
    }
done
grep -q 'ui-dark' "$WORK/schema-extract/usr/share/glib-2.0/schemas/org.dclawos.settings.gschema.xml" || {
    echo "schema lacks the ui-dark key" >&2
    exit 1
}
grep -q 'ui-dark' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings lacks the dark-mode toggle" >&2
    exit 1
}
# Phase 6 M3 supersedes the two Phase 5 chat assertions below in
# their original location: the chat window is now a WebView host
# and rule 4 (blur reveal) + the copy feedback live in the chat
# PAGE (usr/share/dclawos/chat.html), asserted in the M3 block.
unsquashfs -d "$WORK/chatpage-p5-extract" "$FS" usr/share/dclawos/chat.html >/dev/null 2>&1
grep -q 'blur(6px)' "$WORK/chatpage-p5-extract/usr/share/dclawos/chat.html" || {
    echo "chat page does not apply the rule-4 blur reveal" >&2
    exit 1
}
grep -q '已复制' "$WORK/chatpage-p5-extract/usr/share/dclawos/chat.html" || {
    echo "chat page lacks the copy-answer feedback action" >&2
    exit 1
}
grep -q 'build_channels_panel' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings lacks the channels page (Phase 5 WS1)" >&2
    exit 1
}

# Phase 5 module 1: third-party channel runtime (WS1). channellib
# + the Telegram adapter + the channel gate test must be present
# and syntactically valid; the daemon must poll channels and route
# replies through them; the settings GUI must carry the channels
# page. Behavior is gated at build time by hook 1008 (mock Bot API).
for lib in channellib channel_telegram channeltest registrylib; do
    unsquashfs -d "$WORK/${lib}-p5-extract" "$FS" "usr/local/lib/dclawos/${lib}.py" >/dev/null 2>&1
    test -f "$WORK/${lib}-p5-extract/usr/local/lib/dclawos/${lib}.py"
    python3 -m py_compile "$WORK/${lib}-p5-extract/usr/local/lib/dclawos/${lib}.py"
done
grep -q '_poll_channels' "$WORK/dclawos-agent-extract/usr/local/bin/dclawos-agent" || {
    echo "agent daemon does not poll channels" >&2
    exit 1
}
grep -q 'normalize_channel' "$WORK/registrylib-p5-extract/usr/local/lib/dclawos/registrylib.py" || {
    echo "registry lib lacks the channels category" >&2
    exit 1
}
# NOTE: the settings channels-page assertion lives in the Phase 5
# module 5 block above: the settings binary lands with that module.

# Phase 5 module 4: agent self-provisioning (WS2). provisioninglib
# + its gate test must ship; the MCP client must carry the HTTP
# transport + handshake validator; the daemon must dispatch the
# provisioning tools; git must be in the base package set (skill
# git sources). Behavior is gated by hook 1008 (provisiontest).
for lib in provisioninglib provisiontest mcpclient; do
    unsquashfs -d "$WORK/${lib}-p5m4-extract" "$FS" "usr/local/lib/dclawos/${lib}.py" >/dev/null 2>&1
    test -f "$WORK/${lib}-p5m4-extract/usr/local/lib/dclawos/${lib}.py"
    python3 -m py_compile "$WORK/${lib}-p5m4-extract/usr/local/lib/dclawos/${lib}.py"
done
grep -q 'def skill_install' "$WORK/provisioninglib-p5m4-extract/usr/local/lib/dclawos/provisioninglib.py" || {
    echo "provisioninglib lacks skill_install" >&2
    exit 1
}
grep -q 'def channel_manage' "$WORK/provisioninglib-p5m4-extract/usr/local/lib/dclawos/provisioninglib.py" || {
    echo "provisioninglib lacks channel_manage" >&2
    exit 1
}
grep -q 'MCPHttpClient' "$WORK/mcpclient-p5m4-extract/usr/local/lib/dclawos/mcpclient.py" || {
    echo "mcpclient lacks the HTTP transport" >&2
    exit 1
}
grep -q 'provisioninglib' "$WORK/dclawos-agent-extract/usr/local/bin/dclawos-agent" || {
    echo "agent daemon lacks the provisioning dispatch" >&2
    exit 1
}
grep -q 'provisiontest.py' config/hooks/normal/1008-dclawos-agent-loop-test.hook.chroot || {
    echo "hook 1008 lacks the provisiontest call" >&2
    exit 1
}
grep -q '^git$' config/package-lists/dclawos-base.list.chroot || {
    echo "git missing from base packages" >&2
    exit 1
}

# Phase 6 module M1: event bus + bridge protocol + push freshness.
# The libs and the gate test must ship and compile; the daemon must
# serve the bus and wake on inotify; the dashboard/monitor must be
# bus subscribers with NO display polling timers left in actual
# code (comments/docstrings are stripped before the negative greps,
# per the gate rule: assert code, not prose). Behavior is gated by
# hook 1008 (eventbustest).
for lib in eventbus bridgeprotocol eventbustest; do
    unsquashfs -d "$WORK/${lib}-p6m1-extract" "$FS" "usr/local/lib/dclawos/${lib}.py" >/dev/null 2>&1
    test -f "$WORK/${lib}-p6m1-extract/usr/local/lib/dclawos/${lib}.py"
    python3 -m py_compile "$WORK/${lib}-p6m1-extract/usr/local/lib/dclawos/${lib}.py"
done
grep -q 'PROTOCOL_VERSION = 1' "$WORK/eventbus-p6m1-extract/usr/local/lib/dclawos/eventbus.py" || {
    echo "eventbus lacks protocol v1" >&2
    exit 1
}
grep -q 'class EventBusServer' "$WORK/eventbus-p6m1-extract/usr/local/lib/dclawos/eventbus.py" || {
    echo "eventbus lacks the server" >&2
    exit 1
}
grep -q 'SURFACE_COMMANDS' "$WORK/bridgeprotocol-p6m1-extract/usr/local/lib/dclawos/bridgeprotocol.py" || {
    echo "bridgeprotocol lacks the surface allowlists" >&2
    exit 1
}
grep -q 'eventbus' "$WORK/agentlib-extract/usr/local/lib/dclawos/agentlib.py" || {
    echo "agentlib does not publish to the event bus" >&2
    exit 1
}
# (agent daemon already extracted to $WORK/dclawos-agent-extract by
# the Phase 3 block above; dashboard/monitor are extracted here.)
for tool in dclawos-dashboard dclawos-monitor; do
    unsquashfs -d "$WORK/${tool}-p6m1-extract" "$FS" "usr/local/bin/${tool}" >/dev/null 2>&1
    test -x "$WORK/${tool}-p6m1-extract/usr/local/bin/${tool}"
    python3 -m py_compile "$WORK/${tool}-p6m1-extract/usr/local/bin/${tool}"
done
grep -q 'EventBusServer' "$WORK/dclawos-agent-extract/usr/local/bin/dclawos-agent" || {
    echo "agent daemon does not serve the event bus" >&2
    exit 1
}
grep -q 'DirectoryWatcher' "$WORK/dclawos-agent-extract/usr/local/bin/dclawos-agent" || {
    echo "agent daemon lacks the inotify task watcher" >&2
    exit 1
}
grep -q 'EventSubscriber' "$WORK/dclawos-dashboard-p6m1-extract/usr/local/bin/dclawos-dashboard" || {
    echo "dashboard does not subscribe to the event bus" >&2
    exit 1
}
grep -q 'EventSubscriber' "$WORK/dclawos-monitor-p6m1-extract/usr/local/bin/dclawos-monitor" || {
    echo "monitor does not subscribe to the event bus" >&2
    exit 1
}
# Negative checks on stripped code (python comments AND docstrings
# removed by the tokenize-free trick below: drop comment text and
# triple-quoted blocks via a small python filter).
strip_py() {
    python3 - "$1" <<'PYEOF'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
src = re.sub(r'"""[\s\S]*?"""', '""', src)
src = re.sub(r"'''[\s\S]*?'''", "''", src)
lines = []
for line in src.splitlines():
    lines.append(line.split("#", 1)[0])
sys.stdout.write("\n".join(lines))
PYEOF
}
if strip_py "$WORK/dclawos-agent-extract/usr/local/bin/dclawos-agent" | grep -q 'POLL_SECONDS'; then
    echo "agent daemon still carries the timed queue poll" >&2
    exit 1
fi
if strip_py "$WORK/dclawos-dashboard-p6m1-extract/usr/local/bin/dclawos-dashboard" | grep -q 'timeout_add'; then
    echo "dashboard still has a display polling timer" >&2
    exit 1
fi
if strip_py "$WORK/dclawos-dashboard-p6m1-extract/usr/local/bin/dclawos-dashboard" | grep -q 'poll_takeover'; then
    echo "dashboard still polls the takeover state" >&2
    exit 1
fi
if strip_py "$WORK/dclawos-monitor-p6m1-extract/usr/local/bin/dclawos-monitor" | grep -q 'timeout_add'; then
    echo "monitor still has a display polling timer" >&2
    exit 1
fi
grep -q 'eventbustest.py' config/hooks/normal/1008-dclawos-agent-loop-test.hook.chroot || {
    echo "hook 1008 lacks the eventbustest call" >&2
    exit 1
}

# Phase 6 module M2: model settings completeness. The new libs +
# gate test + WebView page must ship and compile; the WebKitGTK 4.1
# typelib must be in the image (the settings host loads the page
# through it); llmlib must carry the routing pass-through symbols;
# the settings host must wire the WebView + bridge; the page must
# follow the palette rules (no pure-black literal, no external
# resources — comments stripped before the negative greps);
# dclawos-llm must carry the --status inspect mode; hook 1008 must
# run modeltest. Behavior is gated by hook 1008 (modeltest).
for lib in modellib usagelib modeltest; do
    unsquashfs -d "$WORK/${lib}-p6m2-extract" "$FS" "usr/local/lib/dclawos/${lib}.py" >/dev/null 2>&1
    test -f "$WORK/${lib}-p6m2-extract/usr/local/lib/dclawos/${lib}.py"
    python3 -m py_compile "$WORK/${lib}-p6m2-extract/usr/local/lib/dclawos/${lib}.py"
done
grep -q 'gir1.2-webkit2-4.1' config/package-lists/dclawos-desktop.list.chroot || {
    echo "package list lacks the WebKitGTK 4.1 package (M2)" >&2
    exit 1
}
grep -qE '(^|/)usr/lib/x86_64-linux-gnu/girepository-1.0/WebKit2-4.1.typelib$' "$LIST" || {
    echo "WebKit2 4.1 typelib missing from final image" >&2
    exit 1
}
unsquashfs -d "$WORK/modelpage-p6m2-extract" "$FS" usr/share/dclawos/model-settings.html >/dev/null 2>&1
test -s "$WORK/modelpage-p6m2-extract/usr/share/dclawos/model-settings.html"
python3 - "$WORK/modelpage-p6m2-extract/usr/share/dclawos/model-settings.html" <<'PYEOF'
import re, sys
page = open(sys.argv[1], encoding="utf-8").read()
stripped = re.sub(r"<!--.*?-->", "", page, flags=re.S)
stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
assert "#000" not in stripped, "model page uses pure black"
for token in ("#f5f5f3", "#fcfcfb", "#0b0b0a", "#151515",
              "#1a1a19", "#20201e"):
    assert token in stripped, f"model page lost palette token {token}"
assert "tabular-nums" in stripped, "model page lacks tabular-nums"
assert "text-wrap:pretty" in stripped.replace(" ", ""), \
    "model page lacks text-wrap:pretty"
assert "grid-template-rows:0fr" in stripped.replace(" ", ""), \
    "model page lacks the Grid 0fr->1fr fold"
assert "box-shadow:inset0001px" in stripped.replace(" ", ""), \
    "model page lacks the focus double ring"
assert not re.search(r'(?:src|href)\s*=\s*"https?://', stripped), \
    "model page references an external resource"
assert "__dclawosReceive" in stripped, "model page lacks the bridge receiver"
assert "model.config.save" in stripped, "model page does not save config via the bridge"
PYEOF
unsquashfs -d "$WORK/llmlib-p6m2-extract" "$FS" usr/local/lib/dclawos/llmlib.py >/dev/null 2>&1
python3 -m py_compile "$WORK/llmlib-p6m2-extract/usr/local/lib/dclawos/llmlib.py"
for sym in build_request resolve_pin test_connection customPricing routerStrategy explorationRate; do
    grep -q "$sym" "$WORK/llmlib-p6m2-extract/usr/local/lib/dclawos/llmlib.py" || {
        echo "llmlib lacks M2 wiring symbol: $sym" >&2
        exit 1
    }
done
grep -q 'WebKit2' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings host does not load WebKit2 (M2 WebView missing)" >&2
    exit 1
}
grep -q '_model_dispatch' "$WORK/settings-extract/usr/local/bin/dclawos-settings" || {
    echo "settings host lacks the M2 bridge dispatch" >&2
    exit 1
}
grep -q 'model.provider.save' "$WORK/bridgeprotocol-p6m1-extract/usr/local/lib/dclawos/bridgeprotocol.py" || {
    echo "bridgeprotocol lacks the M2 model verbs" >&2
    exit 1
}
grep -q -- '--status' "$WORK/llm-extract/usr/local/bin/dclawos-llm" || {
    echo "dclawos-llm lacks the M2 --status inspect mode" >&2
    exit 1
}
grep -q 'normalize_model_config' "$WORK/registrylib-extract/usr/local/lib/dclawos/registrylib.py" || {
    echo "registrylib lacks the M2 model_config validation" >&2
    exit 1
}
grep -q 'modeltest.py' config/hooks/normal/1008-dclawos-agent-loop-test.hook.chroot || {
    echo "hook 1008 lacks the modeltest call" >&2
    exit 1
}

# Phase 6 module M3: chat WebView + true streaming. The chat libs +
# gate test + WebView page must ship and compile; the page must
# execute the iron rules in native web form (palette, no pure
# black, rule-4 90ms blur(6px) reveal with the binding easing,
# tabular-nums, text-wrap:pretty, Grid fold, 2.5s feedback — all
# on comment-stripped text); the chat host must subscribe to the
# event bus with NO polling timer and NO event-log file read left
# in stripped code; llmlib / dclawos-llm must carry the streaming
# symbols; the engine source must carry the SSE executor; the
# bridge registry must carry the M3 chat verbs; hook 1008 must
# run chattest. Behavior is gated by hook 1008 (chattest).
for lib in chatlib chattest; do
    unsquashfs -d "$WORK/${lib}-p6m3-extract" "$FS" "usr/local/lib/dclawos/${lib}.py" >/dev/null 2>&1
    test -f "$WORK/${lib}-p6m3-extract/usr/local/lib/dclawos/${lib}.py"
    python3 -m py_compile "$WORK/${lib}-p6m3-extract/usr/local/lib/dclawos/${lib}.py"
done
grep -q 'class ChatHost' "$WORK/chatlib-p6m3-extract/usr/local/lib/dclawos/chatlib.py" || {
    echo "chatlib lacks the ChatHost" >&2
    exit 1
}
grep -q 'def parse_command' "$WORK/chatlib-p6m3-extract/usr/local/lib/dclawos/chatlib.py" || {
    echo "chatlib lacks the transplanted parse_command" >&2
    exit 1
}
grep -q 'def call_stream' "$WORK/llmlib-p6m2-extract/usr/local/lib/dclawos/llmlib.py" || {
    echo "llmlib lacks the M3 streaming call" >&2
    exit 1
}
grep -q -- '--stream' "$WORK/llm-extract/usr/local/bin/dclawos-llm" || {
    echo "dclawos-llm lacks the M3 --stream mode" >&2
    exit 1
}
unsquashfs -d "$WORK/llmts-p6m3-extract" "$FS" usr/share/dclawos/engine-src/dclawos/llm.ts >/dev/null 2>&1
grep -q 'executeStreamTarget' "$WORK/llmts-p6m3-extract/usr/share/dclawos/engine-src/dclawos/llm.ts" || {
    echo "engine llm.ts lacks the SSE streaming executor" >&2
    exit 1
}
grep -q 'stream_mode' "$WORK/llmts-p6m3-extract/usr/share/dclawos/engine-src/dclawos/llm.ts" || {
    echo "engine llm.ts lacks the stream_mode envelope field" >&2
    exit 1
}
grep -q 'chat.model.set' "$WORK/bridgeprotocol-p6m1-extract/usr/local/lib/dclawos/bridgeprotocol.py" || {
    echo "bridgeprotocol lacks the M3 chat verbs" >&2
    exit 1
}
grep -q 'EventSubscriber' "$WORK/dclawos-chat-extract/usr/local/bin/dclawos-chat" || {
    echo "chat host does not subscribe to the event bus" >&2
    exit 1
}
grep -q 'WebKit2' "$WORK/dclawos-chat-extract/usr/local/bin/dclawos-chat" || {
    echo "chat host does not load WebKit2 (M3 WebView missing)" >&2
    exit 1
}
if strip_py "$WORK/dclawos-chat-extract/usr/local/bin/dclawos-chat" | grep -q 'timeout_add'; then
    echo "chat host still has a display polling timer" >&2
    exit 1
fi
if strip_py "$WORK/dclawos-chat-extract/usr/local/bin/dclawos-chat" | grep -q 'events_path'; then
    echo "chat host still reads the event log file" >&2
    exit 1
fi
python3 - "$WORK/chatpage-p5-extract/usr/share/dclawos/chat.html" <<'PYEOF'
import re, sys
page = open(sys.argv[1], encoding="utf-8").read()
stripped = re.sub(r"<!--.*?-->", "", page, flags=re.S)
stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
assert "#000" not in stripped, "chat page uses pure black"
for token in ("#f5f5f3", "#fcfcfb", "#0b0b0a", "#151515",
              "#1a1a19", "#20201e"):
    assert token in stripped, f"chat page lost palette token {token}"
assert "90ms" in stripped, "chat page lacks the rule-4 90ms reveal"
assert "blur(6px)" in stripped, "chat page lacks the rule-4 blur(6px)"
assert "cubic-bezier(.215,.61,.355,1)" in stripped, \
    "chat page lacks the binding easing"
assert "tabular-nums" in stripped, "chat page lacks tabular-nums"
assert "text-wrap:pretty" in stripped.replace(" ", ""), \
    "chat page lacks text-wrap:pretty"
assert "grid-template-rows:0fr" in stripped.replace(" ", ""), \
    "chat page lacks the Grid 0fr->1fr fold"
assert "vertical-align:-0.125em" in stripped.replace(" ", ""), \
    "chat page lacks the inline-icon alignment"
assert "2500" in stripped, "chat page lacks the 2.5s feedback timer"
assert not re.search(r'(?:src|href)\s*=\s*"https?://', stripped), \
    "chat page references an external resource"
assert "__dclawosReceive" in stripped, \
    "chat page lacks the bridge receiver"
assert "chat.approve" in stripped, \
    "chat page does not decide approvals via the bridge"
assert "chat.model.set" in stripped, \
    "chat page does not switch models via the bridge"
PYEOF
grep -q 'chattest.py' config/hooks/normal/1008-dclawos-agent-loop-test.hook.chroot || {
    echo "hook 1008 lacks the chattest call" >&2
    exit 1
}

# Phase 6 module M4: dashboard WebView + approval overlay +
# taskbar agent state. The dashboard lib + gate test + WebView
# page must ship and compile; the page must execute the iron
# rules in native web form (palette, no pure black, tabular-nums,
# text-wrap:pretty, Grid fold, focus double ring, inline-icon
# alignment, 2.5s feedback — all on comment-stripped text) and
# call the M4 bridge verbs; the dashboard host must subscribe to
# the event bus with NO display polling timer in stripped code;
# the taskbar must keep its frozen geometry (TASKBAR_HEIGHT = 48
# and the _NET_WM_STRUT_PARTIAL reservation) while carrying the
# dashboardlib reducer wiring; the bridge registry must carry
# the M4 dashboard verbs; hook 1008 must run dashboardtest.
# Behavior is gated by hook 1008 (dashboardtest).
for lib in dashboardlib dashboardtest; do
    unsquashfs -d "$WORK/${lib}-p6m4-extract" "$FS" "usr/local/lib/dclawos/${lib}.py" >/dev/null 2>&1
    test -f "$WORK/${lib}-p6m4-extract/usr/local/lib/dclawos/${lib}.py"
    python3 -m py_compile "$WORK/${lib}-p6m4-extract/usr/local/lib/dclawos/${lib}.py"
done
grep -q 'class DashboardHost' "$WORK/dashboardlib-p6m4-extract/usr/local/lib/dclawos/dashboardlib.py" || {
    echo "dashboardlib lacks the DashboardHost" >&2
    exit 1
}
grep -q 'def reduce_shell_state' "$WORK/dashboardlib-p6m4-extract/usr/local/lib/dclawos/dashboardlib.py" || {
    echo "dashboardlib lacks the shell state reducer" >&2
    exit 1
}
grep -q 'def merge_events' "$WORK/dashboardlib-p6m4-extract/usr/local/lib/dclawos/dashboardlib.py" || {
    echo "dashboardlib lacks the snapshot/live merge" >&2
    exit 1
}
grep -q 'apply_verdict' "$WORK/dashboardlib-p6m4-extract/usr/local/lib/dclawos/dashboardlib.py" || {
    echo "dashboardlib does not use the shared verdict write" >&2
    exit 1
}
unsquashfs -d "$WORK/dashpage-p6m4-extract" "$FS" usr/share/dclawos/dashboard.html >/dev/null 2>&1
test -s "$WORK/dashpage-p6m4-extract/usr/share/dclawos/dashboard.html"
python3 - "$WORK/dashpage-p6m4-extract/usr/share/dclawos/dashboard.html" <<'PYEOF'
import re, sys
page = open(sys.argv[1], encoding="utf-8").read()
stripped = re.sub(r"<!--.*?-->", "", page, flags=re.S)
stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
assert "#000" not in stripped, "dashboard page uses pure black"
for token in ("#f5f5f3", "#fcfcfb", "#0b0b0a", "#151515",
              "#1a1a19", "#20201e"):
    assert token in stripped, f"dashboard page lost palette token {token}"
assert "tabular-nums" in stripped, "dashboard page lacks tabular-nums"
assert "text-wrap:pretty" in stripped.replace(" ", ""), \
    "dashboard page lacks text-wrap:pretty"
assert "grid-template-rows:0fr" in stripped.replace(" ", ""), \
    "dashboard page lacks the Grid 0fr->1fr fold"
assert "box-shadow:inset0001px" in stripped.replace(" ", ""), \
    "dashboard page lacks the focus double ring"
assert "vertical-align:-0.125em" in stripped.replace(" ", ""), \
    "dashboard page lacks the inline-icon alignment"
assert "2500" in stripped, "dashboard page lacks the 2.5s feedback timer"
assert not re.search(r'(?:src|href)\s*=\s*"https?://', stripped), \
    "dashboard page references an external resource"
assert "__dclawosReceive" in stripped, \
    "dashboard page lacks the bridge receiver"
assert "dashboard.state" in stripped, \
    "dashboard page does not load state via the bridge"
assert "dashboard.approve" in stripped, \
    "dashboard page does not decide approvals via the bridge"
assert "dashboard.launch" in stripped, \
    "dashboard page does not launch apps via the bridge"
PYEOF
grep -q 'WebKit2' "$WORK/dclawos-dashboard-p6m1-extract/usr/local/bin/dclawos-dashboard" || {
    echo "dashboard host does not load WebKit2 (M4 WebView missing)" >&2
    exit 1
}
if strip_py "$WORK/dclawos-dashboard-p6m1-extract/usr/local/bin/dclawos-dashboard" | grep -q 'timeout_add'; then
    echo "dashboard host still has a display polling timer" >&2
    exit 1
fi
grep -q 'dashboard.approve' "$WORK/bridgeprotocol-p6m1-extract/usr/local/lib/dclawos/bridgeprotocol.py" || {
    echo "bridgeprotocol lacks the M4 dashboard verbs" >&2
    exit 1
}
grep -q 'TASKBAR_HEIGHT = 48' "$WORK/theme-shell/usr/local/bin/dclawos-shell" || {
    echo "taskbar height constant changed (M4 must not move geometry)" >&2
    exit 1
}
grep -q '_NET_WM_STRUT_PARTIAL' "$WORK/theme-shell/usr/local/bin/dclawos-shell" || {
    echo "taskbar lost the strut reservation" >&2
    exit 1
}
grep -q 'reduce_shell_state' "$WORK/theme-shell/usr/local/bin/dclawos-shell" || {
    echo "taskbar does not reduce bus events via dashboardlib (M4)" >&2
    exit 1
}
grep -q 'initial_shell_state' "$WORK/theme-shell/usr/local/bin/dclawos-shell" || {
    echo "taskbar does not seed its agent state from the stores (M4)" >&2
    exit 1
}
grep -q 'dashboardtest.py' config/hooks/normal/1008-dclawos-agent-loop-test.hook.chroot || {
    echo "hook 1008 lacks the dashboardtest call" >&2
    exit 1
}

# Initramfs must contain live-boot (the live session depends on it).

lsinitramfs "$INITRD" | grep -q 'scripts/live' || {
    echo "initramfs lacks live-boot scripts" >&2
    exit 1
}

echo "final ISO validation passed: $ISO"
