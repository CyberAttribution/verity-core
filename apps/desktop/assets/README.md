# macOS application icon

`verity-eye-fullframe.png` is the unchanged original brand artwork. The native AppKit compositor in `tools/desktop/build_app_icon.swift` scales it uniformly into an inset black rounded tile on a transparent canvas. At 1024 pixels the tile is 864 square, inset 80, with 190-pixel corner radii and a further 16-pixel artwork inset. No logo tracing or generative replacement is used.

Regenerate on macOS with `python3 tools/desktop/build_app_icon.py`. This compiles the compositor with the same isolated duplicate-Swift-module workaround used by native packaging; it never changes system module maps. `iconutil` may require the permitted unsandboxed execution mechanism. All ten standard 16/32/128/256/512 point 1x/2x PNG representations are retained in `AppIcon.iconset`; `AppIcon.icns` is the packaged output. Validate transparency and native Dock/Finder appearance after regeneration. The icon change does not alter app authority or runtime behavior.
