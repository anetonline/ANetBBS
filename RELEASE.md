# ANetBBS v1.0a2.141 — BBS Directory: correct TelnetBBSGuide + IPTIA formats

## Changes

### TelnetBBSGuide: ZIP + pipe-delimited text (not CSV)

The TelnetBBSGuide list is a monthly ZIP file (`ibbs{MM}{YYYY}.zip`), not a
plain CSV. The URL is now constructed dynamically from the current month/year,
with automatic fallback to the previous month if the current one returns 404.
The ZIP is extracted in memory and the text file inside is parsed as
pipe-delimited rows. Column detection is flexible — uses header row if
present, positional fallback otherwise.

### IPTIA: XML (not CSV)

IPTIA publishes `dialdirectory.xml`. Parsed with `xml.etree.ElementTree`,
trying a broad set of common tag names (name, address, telnet, port, sysop,
city, state, country, software, web, description, etc.) so the parser works
regardless of exact element naming. Host:port combined fields are split
automatically.

### Both sources: robust error handling

All fetch, parse, and DB-insert steps are individually wrapped in try/except.
Malformed files, network errors, or unknown formats silently return 0 entries
and do not crash the background refresh thread.
