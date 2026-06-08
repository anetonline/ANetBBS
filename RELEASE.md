# ANetBBS v1.0a2.95 — File browser page size fix (80x25)

## What's new

### Fix: file browser shows only items 10-20, items 1-9 scroll off the top

File browser page size was 20.  Most files have a 2-line entry (filename row
+ description row), so a full page = 40+ content lines — far more than a
standard 80×25 terminal can display.

Reduced PAGE from 20 to 9.  Worst case (all files have descriptions):
  4 header lines + 9×2 content lines + 2 nav lines = 24 lines.
Fits comfortably in an 80×25 terminal with one line to spare.
