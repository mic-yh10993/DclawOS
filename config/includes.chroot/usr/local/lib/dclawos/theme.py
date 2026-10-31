#!/usr/bin/env python3
"""Shared DclawOS GUI theme (Phase 3 module 7; Phase 5 module 5;
Phase 6 M5: dual-generation — GTK4 apps get theme-gtk4.css via
add_provider_for_display, the GTK3 taskbar keeps theme.css;
helpers branch on the loaded Gtk major version).

One small module every DclawOS GTK window app imports: it loads
the shared stylesheet (/usr/share/dclawos/theme.css) through a
Gtk.CssProvider, falling back to an embedded copy of the same CSS
when the file is missing. When the shared settings file key
ui-dark is true (settingslib, P5-M7), the dark variant
(theme-dark.css, same fallback pattern) is appended after the
base sheet. It NEVER raises: any failure returns False and the
app keeps its default look. A theme failure must never break an
app.

Phase 5: the stylesheet implements the user's 10 iron rules as
mapped in docs/UI-RULES.md (palette layers, focus double-ring,
tabular numbers, feedback fade), and this module carries the
code-side helpers the rules need:

- stream_reveal(): rule 4 — staged, staggered reveal of a chat
  reply in a GtkTextView (alpha ramp per chunk; GTK has no glyph
  blur, see UI-RULES.md for the deviation note).
- flash_feedback(): rules 9+10 — "✓ …" feedback that pulses once
  (opacity channel; GTK3 has no scale transform) and restores
  itself after 2.5 s.
- inline_icon(): rule 5 — inline image alignment helper.
- apply_tnum(): rule 6 — adds the .numeric CSS class.
- apply_prose(): rule 8 — wrap + line-length cap (nearest
  equivalent of text-wrap: pretty in Pango).
- make_section(): rule 7 — a header button + Gtk.Revealer
  collapsible section (layout-driven height animation; app code
  never measures heights).

The taskbar (dclawos-shell) DOES apply the theme since Phase 5:
its window carries the .dclawos-taskbar class, and the stylesheet
scopes dock button padding under that class so the strut layout
is untouched — palette and typography align, geometry does not
move (the Phase 3 exclusion was about padding, not colour).
"""

import os

CSS_PATH = "/usr/share/dclawos/theme.css"
DARK_CSS_PATH = "/usr/share/dclawos/theme-dark.css"
# Phase 6 M5: GTK4 processes use the GTK4 sheets (same tokens,
# GTK4 node names); GTK3 processes (the Wnck taskbar exception)
# keep the sheets above. Token parity is gate-asserted.
CSS_PATH_GTK4 = "/usr/share/dclawos/theme-gtk4.css"
DARK_CSS_PATH_GTK4 = "/usr/share/dclawos/theme-gtk4-dark.css"
DARK_KEY = "ui-dark"  # shared settings file key (settingslib)

# Embedded copies of theme.css / theme-dark.css (kept in sync by
# hand; the files win when present). Duplication is intentional:
# theming must survive a missing file, and the fallback must
# match the file exactly.
FALLBACK_CSS = """/* DclawOS shared GUI theme (Phase 5 module 5: the user's 10 iron
   rules, mapped in docs/UI-RULES.md). Palette tokens — light:
   layer-0 canvas #f5f5f3, layer-1 panel #fcfcfb, ink #1a1a19.
   Separation is by 1-3% lightness steps and inset hairlines in the
   neighbouring layer's tone, never by contrasting borders; pure
   black appears nowhere. Accent (focus rings) is the deepened
   brand blue #3d5a80. theme.py embeds a copy of this file as a
   fallback; keep the two in sync when editing. */

window {
    background-color: #f5f5f3;
    color: #1a1a19;
}
headerbar {
    background-color: #fcfcfb;
    color: #1a1a19;
    border: none;
    box-shadow: inset 0 -1px #e8e8e5;
}
headerbar label { color: #1a1a19; }
headerbar button {
    background-color: transparent;
    color: #1a1a19;
    border: none;
    border-radius: 8px;
    box-shadow: none;
}
headerbar button:hover { background-color: #efefec; }

button {
    background-color: #fcfcfb;
    color: #1a1a19;
    border: none;
    border-radius: 10px;
    padding: 6px 14px;
    box-shadow: inset 0 0 0 1px rgba(26, 26, 25, 0.07);
    transition: background-color 160ms cubic-bezier(.215, .61, .355, 1);
}
button:hover { background-color: #f0f0ed; }
button:active { background-color: #e9e9e6; }
button:disabled { color: rgba(26, 26, 25, 0.4); }

entry {
    background-color: #fcfcfb;
    color: #1a1a19;
    border: none;
    border-radius: 10px;
    padding: 7px 10px;
    box-shadow: inset 0 0 0 1px rgba(26, 26, 25, 0.09);
    transition: box-shadow 160ms cubic-bezier(.215, .61, .355, 1);
}
/* Iron rule 2: focus never changes the border and never draws an
   outline; it deepens a double box-shadow ring instead. */
entry:focus, spinbutton:focus {
    box-shadow: inset 0 0 0 2px #3d5a80,
                0 0 0 4px rgba(61, 90, 128, 0.25);
}
textview {
    background-color: #fcfcfb;
    color: #1a1a19;
}
textview:focus {
    box-shadow: inset 0 0 0 2px #3d5a80,
                0 0 0 4px rgba(61, 90, 128, 0.25);
}
treeview {
    background-color: #fcfcfb;
    color: #1a1a19;
    font-feature-settings: "tnum" 1;
}
treeview header button {
    background-color: #f5f5f3;
    border: none;
    box-shadow: inset 0 -1px #e8e8e5;
    padding: 4px 8px;
}
scrolledwindow { border: none; }
frame { border: none; }
separator { background-color: #e8e8e5; }

selection {
    background-color: #3d5a80;
    color: #fcfcfb;
}
menu, popover {
    background-color: #fcfcfb;
    color: #1a1a19;
}
tooltip {
    background-color: #20201e;
    color: #f5f5f3;
    border: none;
    border-radius: 8px;
}

/* Iron rule 6: tabular numbers for readouts (treeviews above get
   it globally; labels opt in with this class). */
.numeric { font-feature-settings: "tnum" 1; }

/* Iron rule 10: feedback fade uses the rule-4 easing curve. */
.feedback-fade {
    transition: opacity 250ms cubic-bezier(.215, .61, .355, 1);
}

/* Taskbar (dclawos-shell): palette + typography only. The window
   class scopes button padding down so the dock's fixed height and
   strut geometry are untouched (the Phase 3 layout exclusion was
   about padding, not colour). */
window.dclawos-taskbar {
    background-color: #fcfcfb;
}
window.dclawos-taskbar button {
    padding: 3px 10px;
    border-radius: 8px;
}
""".encode("utf-8")

FALLBACK_DARK_CSS = """/* DclawOS dark variant (Phase 5 module 5). Appended AFTER
   theme.css by theme.py when org.dclawos.settings ui-dark is
   true. Palette tokens — dark: layer-0 #0b0b0a, layer-1 #151515,
   layer-2 #1a1a19, layer-3 #20201e, ink #f5f5f3. Iron rule 3:
   font weights shift down 40 (400->360, 600->560, 700->660) so
   light text does not look bloated on dark surfaces; on the
   shipped non-variable fonts Pango rounds to the nearest real
   face (see docs/UI-RULES.md for the honest deviation note).
   theme.py embeds a copy as fallback; keep in sync. */

window {
    background-color: #0b0b0a;
    color: #f5f5f3;
    font-weight: 360;
}
headerbar {
    background-color: #151515;
    color: #f5f5f3;
    box-shadow: inset 0 -1px #20201e;
}
headerbar label { color: #f5f5f3; }
headerbar .title { font-weight: 560; }
headerbar button {
    background-color: transparent;
    color: #f5f5f3;
}
headerbar button:hover { background-color: #1a1a19; }

button {
    background-color: #1a1a19;
    color: #f5f5f3;
    font-weight: 360;
    box-shadow: inset 0 0 0 1px rgba(245, 245, 243, 0.07);
}
button:hover { background-color: #20201e; }
button:active { background-color: #151515; }
button:disabled { color: rgba(245, 245, 243, 0.4); }

label { font-weight: 360; }
entry {
    background-color: #151515;
    color: #f5f5f3;
    font-weight: 360;
    box-shadow: inset 0 0 0 1px rgba(245, 245, 243, 0.09);
}
entry:focus, spinbutton:focus {
    box-shadow: inset 0 0 0 2px #7ea6d8,
                0 0 0 4px rgba(126, 166, 216, 0.28);
}
textview {
    background-color: #151515;
    color: #f5f5f3;
    font-weight: 360;
}
textview:focus {
    box-shadow: inset 0 0 0 2px #7ea6d8,
                0 0 0 4px rgba(126, 166, 216, 0.28);
}
treeview {
    background-color: #151515;
    color: #f5f5f3;
    font-weight: 360;
}
treeview header button {
    background-color: #0b0b0a;
    color: #f5f5f3;
    box-shadow: inset 0 -1px #20201e;
}
separator { background-color: #20201e; }
menu, popover {
    background-color: #20201e;
    color: #f5f5f3;
}
tooltip {
    background-color: #20201e;
    color: #f5f5f3;
}
window.dclawos-taskbar {
    background-color: #151515;
}
window.dclawos-taskbar button {
    background-color: #1a1a19;
}
""".encode("utf-8")


def _read_css(path, fallback):
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        if data.strip():
            return data
    except OSError:
        pass
    return fallback


def _dark_preferred():
    """ui-dark from the shared settings file (settingslib, P5-M7);
    env override as fallback; False on any problem."""
    try:
        import settingslib  # same lib dir as this module
        return bool(settingslib.get_setting(DARK_KEY))
    except Exception:
        return os.environ.get("DCLAWOS_UI_DARK") == "1"


def _gtk_major():
    """Major version of the Gtk this process already loaded (the
    app chooses it via gi.require_version before importing us);
    None when Gtk is unavailable."""
    try:
        from gi.repository import Gtk
        return Gtk.get_major_version()
    except Exception:
        return None


def apply_theme():
    """Apply the shared stylesheet to this process. Returns bool.

    Phase 6 M5: dual-generation. A GTK4 process gets the GTK4
    sheets via StyleContext.add_provider_for_display; a GTK3
    process (dclawos-shell, the Wnck exception) keeps the GTK3
    sheets via add_provider_for_screen. No require_version is
    pinned here: the app process has already chosen its Gtk.
    """
    try:
        from gi.repository import Gtk, Gdk
    except Exception:
        return False
    try:
        major = Gtk.get_major_version()
        if major == 4:
            css = _read_css(CSS_PATH_GTK4, FALLBACK_CSS)
            if _dark_preferred():
                css = css + b"\n" + _read_css(
                    DARK_CSS_PATH_GTK4, FALLBACK_DARK_CSS)
            provider = Gtk.CssProvider()
            provider.load_from_data(css, -1)
            display = Gdk.Display.get_default()
            if display is None:
                return False
            Gtk.StyleContext.add_provider_for_display(
                display, provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            return True
        css = _read_css(CSS_PATH, FALLBACK_CSS)
        if _dark_preferred():
            css = css + b"\n" + _read_css(DARK_CSS_PATH,
                                          FALLBACK_DARK_CSS)
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


# ------------------------------------------------------------ helpers
# Every helper is defensive: theming/interaction polish must never
# break the app that calls it, so failures degrade to the plain
# behaviour (full text at once, static label, no collapse).

def _glib():
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    return GLib


def apply_tnum(widget):
    """Rule 6: tabular numbers via the .numeric CSS class."""
    try:
        widget.get_style_context().add_class("numeric")
        return True
    except Exception:
        return False


def inline_icon(image, pixel_size=16):
    """Rule 5: inline icon alignment (GTK has no vertical-align;
    box-centering at a fixed pixel size is the equivalent)."""
    try:
        from gi.repository import Gtk
        image.set_pixel_size(pixel_size)
        image.set_valign(Gtk.Align.CENTER)
        image.set_halign(Gtk.Align.CENTER)
        return True
    except Exception:
        return False


def apply_prose(label, chars=68):
    """Rule 8 (approximation): wrap + capped line length."""
    try:
        from gi.repository import Gtk, Pango
        if Gtk.get_major_version() == 4:
            # GTK4 renamed the Label wrap properties.
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        else:
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(chars)
        return True
    except Exception:
        return False


def make_section(title, content, expanded=True):
    """Rule 7: collapsible section = flat header button + a
    Gtk.Revealer (SLIDE_DOWN, 250 ms). The layout engine animates
    the height; callers never measure or set pixel heights.
    Returns the container widget to pack."""
    try:
        from gi.repository import Gtk
    except Exception:
        return content
    try:
        gtk4 = Gtk.get_major_version() == 4
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        revealer = Gtk.Revealer()
        revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN)
        revealer.set_transition_duration(250)
        revealer.set_reveal_child(expanded)
        header = Gtk.Button(label=("▾ " if expanded else "▸ ") + title)
        header.set_halign(Gtk.Align.START)

        def _toggle(_button):
            now = not revealer.get_reveal_child()
            revealer.set_reveal_child(now)
            header.set_label(("▾ " if now else "▸ ") + title)

        header.connect("clicked", _toggle)
        if gtk4:
            # GTK4: set_child/append; ReliefStyle is gone, the
            # "flat" style class is its replacement.
            revealer.set_child(content)
            header.add_css_class("flat")
            revealer.set_vexpand(True)
            box.append(header)
            box.append(revealer)
        else:
            revealer.add(content)
            header.set_relief(Gtk.ReliefStyle.NONE)
            box.pack_start(header, False, False, 0)
            box.pack_start(revealer, True, True, 0)
        return box
    except Exception:
        return content


def flash_feedback(widget, ok_text, restore_text=None, ms=2500):
    """Rules 9+10: show ok_text on a button/label, pulse once in
    the opacity channel (under-damped character; GTK3 cannot
    scale widgets), then restore the original text after `ms`
    milliseconds (default exactly the user's 2500)."""
    try:
        GLib = _glib()
    except Exception:
        return False
    try:
        if restore_text is None:
            restore_text = widget.get_label() \
                if hasattr(widget, "get_label") else widget.get_text()
        setter = widget.set_label \
            if hasattr(widget, "set_label") else widget.set_text
    except Exception:
        return False

    def _pulse():
        try:
            widget.set_opacity(0.55)
            GLib.timeout_add(90, _pulse_back)
        except Exception:
            pass
        return False

    def _pulse_back():
        try:
            widget.set_opacity(1.0)
        except Exception:
            pass
        return False

    def _restore():
        try:
            setter(restore_text)
        except Exception:
            pass
        return False

    try:
        setter(ok_text)
        GLib.timeout_add(10, _pulse)
        GLib.timeout_add(ms, _restore)
        return True
    except Exception:
        return False


def stream_reveal(text_view, text, scroll_cb=None):
    """Rule 4: reveal `text` at the end of a GtkTextView buffer
    with the user's timing: characters commit on a 30 ms stagger,
    each passing a ~90 ms ramp from blurred to clear — rendered
    here as a 3-step foreground-alpha ramp (GTK TextView cannot
    blur glyphs; see docs/UI-RULES.md). Long texts commit several
    characters per tick so the total reveal stays bounded (~4 s);
    within a batch every character still gets its own ramp tags.
    Falls back to an instant insert on any problem."""
    try:
        GLib = _glib()
        from gi.repository import Gdk
    except Exception:
        try:
            buffer = text_view.get_buffer()
            buffer.insert(buffer.get_end_iter(), text)
        except Exception:
            pass
        return False
    try:
        buffer = text_view.get_buffer()

        def _fade_tag(name, alpha):
            tag = buffer.get_tag_table().lookup(name)
            if tag is None:
                rgba = Gdk.RGBA()
                rgba.parse("#1a1a19")
                rgba.alpha = alpha
                tag = buffer.create_tag(name, foreground_rgba=rgba)
            return tag

        tags = [_fade_tag("dclawos-fade-1", 0.25),
                _fade_tag("dclawos-fade-2", 0.6)]
        if not text:
            return True
        per_tick = max(1, -(-len(text) // 100))
        state = {"pos": 0, "spans": []}

        def _shift_fades():
            # Age every live span one step: fade-1 -> fade-2 -> plain.
            for span in state["spans"]:
                start, end, step = span
                if step >= 2:
                    continue
                start_iter = buffer.get_iter_at_offset(start)
                end_iter = buffer.get_iter_at_offset(end)
                # GTK4 returns (success, iter) tuples here.
                if isinstance(start_iter, tuple):
                    start_iter = start_iter[1]
                if isinstance(end_iter, tuple):
                    end_iter = end_iter[1]
                if step == 0:
                    buffer.remove_tag(tags[0], start_iter, end_iter)
                    buffer.apply_tag(tags[1], start_iter, end_iter)
                else:
                    buffer.remove_tag(tags[1], start_iter, end_iter)
                span[2] = step + 1
            state["spans"] = [s for s in state["spans"] if s[2] < 2]

        def _tick():
            _shift_fades()
            if state["pos"] >= len(text):
                # Two extra beats let the last batch finish its ramp.
                if any(s[2] < 2 for s in state["spans"]):
                    return True
                return False
            chunk = text[state["pos"]:state["pos"] + per_tick]
            start_offset = buffer.get_char_count()
            buffer.insert(buffer.get_end_iter(), chunk)
            end_offset = buffer.get_char_count()
            start_iter = buffer.get_iter_at_offset(start_offset)
            end_iter = buffer.get_iter_at_offset(end_offset)
            if isinstance(start_iter, tuple):
                start_iter = start_iter[1]
            if isinstance(end_iter, tuple):
                end_iter = end_iter[1]
            buffer.apply_tag(tags[0], start_iter, end_iter)
            state["spans"].append([start_offset, end_offset, 0])
            state["pos"] += per_tick
            if scroll_cb is not None:
                try:
                    scroll_cb()
                except Exception:
                    pass
            return True

        GLib.timeout_add(30, _tick)
        return True
    except Exception:
        try:
            buffer = text_view.get_buffer()
            buffer.insert(buffer.get_end_iter(), text)
        except Exception:
            pass
        return False
