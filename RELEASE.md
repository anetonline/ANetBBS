# ANetBBS v1.0a2.142 — BBS Directory: correct EtherTerm XML parser

## Changes

Both TelnetBBSGuide and IPTIA use the identical EtherTerm phonebook format:

    <BBS name="BBS Name" ip="host.example.com" port="23" protocol="TELNET" ... />

Previous parsers (CSV, pipe-delimited, ElementTree XML) were all wrong.
ElementTree also fails on BBS names containing unescaped `&` (e.g. "Bits & Bytes BBS").

New shared `_parse_etherterm_xml()` uses regex to extract attributes from
`<BBS ... />` lines directly — immune to malformed XML. Tested against live data:
- TelnetBBSGuide ZIP (`ibbs{MM}{YYYY}.zip`): extracts `dialdirectory.xml` from
  inside the archive, parses 1,075 entries
- IPTIA (`dialdirectory.xml`): fetched directly, parses 1,810 entries
