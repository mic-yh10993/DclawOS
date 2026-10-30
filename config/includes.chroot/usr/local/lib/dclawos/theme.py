#!/usr/bin/env python3
"""Shared DclawOS GUI theme (Phase 3 module 7).

One small module every DclawOS GTK window app imports: it loads the
shared stylesheet (/usr/share/dclawos/theme.css) through a
Gtk.CssProvider, falling back to an embedded copy of the same CSS
when the file is missing. It NEVER raises: if Gtk is unavailable, if
there is no screen yet, or if the CSS fails to parse, apply_theme()
returns False and the app keeps its default look. A theme failure
must never break an app.

Deliberately restrained: the stylesheet only sets a light window
background, dark-blue headerbars, comfortable button/entry padding
and rounded corners. It does not restyle individual app layouts,
and it is NOT applied to the taskbar (dclawos-shell): the taskbar
is a height-constrained dock, and button padding changes could
break its strut layout. CLI tools (dclawos-ticket, dclawos-gui)
have no windows and do not import this module.
"""

import os

CSS_PATH = "/usr/share/dclawos/theme.css"

# Embedded copy of theme.css (kept in sync by hand; the file wins
# when present). Duplication is intentional: theming must survive a
# missing file, and the fallback must match the file exactly.
FALLBACK_CSS = b"""
window { background-color: #f5f5f5; }
headerbar {
    background-color: #1f3a5f;
    color: #ffffff;
    border: none;
}
headerbar label { color: #ffffff; }
headerbar button {
    background-color: transparent;
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.35);
    border-radius: 6px;
}
button {
    border-radius: 6px;
    padding: 4px 12px;
}
entry {
    border-radius: 6px;
    padding: 5px 8px;
}
textview { background-color: #ffffff; }
treeview { background-color: #ffffff; }
"""


def apply_theme():
    """Apply the shared stylesheet to this process. Returns bool."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gdk
    except Exception:
        return False
    try:
        css = FALLBACK_CSS
        try:
            with open(CSS_PATH, "rb") as handle:
                data = handle.read()
            if data.strip():
                css = data
        except OSError:
            pass
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        if screen is None:
            return False
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        return True
    except Exception:
        return False
