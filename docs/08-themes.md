# Themes

ANetBBS ships seven hand-picked themes, all WCAG-AA-readable on
body text:

- **Modern Dark** (default) — high-contrast Catppuccin-style palette
- **Classic Green** — green-on-black BBS terminal look
- **Amber Terminal** — warm amber on dark brown
- **Blue Ice** — cyan on deep navy
- **Matrix** — bright neon green on pure black
- **Synthwave** — hot pink + cyan on dark purple
- **Paper White** — light theme for daytime

## Per-user

**Profile → Themes** lets the user pick. Choice is sticky.

## Custom themes

**Admin → Themes** has a builder where the sysop picks colors with
hex pickers. The full set of CSS custom properties:

```
--theme-bg            page background
--theme-bg-dark       darker bg (cards, modals)
--theme-primary       accent / borders
--theme-primary-dark  hover / active accent
--theme-text          body text
--theme-text-muted    secondary labels
--theme-card-bg       card surface
--theme-input-bg      form fields
--theme-input-focus   focused field
--theme-border        misc borders
```

The theme builder writes all 10 so you don't need to touch CSS.

## Toggle default

Mark one theme `is_default=True` to make it the choice for users
who haven't picked one yet.
