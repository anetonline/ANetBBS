# Importing an existing BBS file base

`tools/bbs-files-tool.py` builds the `.descriptions.json` cache used by
ANetBBS from a traditional `FILES.BBS` or `FILES.BSS` file.

The original index file is never modified, renamed, hidden, or deleted.

## Preview

```bash
python3 tools/bbs-files-tool.py -bd \
  --filebase /opt/anetbbs/data/files/OS2.BBS \
  --file.bbs /opt/anetbbs/data/files/OS2.BBS/FILES.BSS \
  --dry-run
```

## Build the cache

```bash
sudo python3 tools/bbs-files-tool.py -bd \
  --filebase /opt/anetbbs/data/files/OS2.BBS \
  --file.bbs /opt/anetbbs/data/files/OS2.BBS/FILES.BSS
```

The output is written to:

```text
/opt/anetbbs/data/files/OS2.BBS/.descriptions.json
```

If an older cache exists, the tool creates a timestamped backup before
replacing it atomically.

## Description sources

Descriptions are selected in this order:

1. Matching entry in `FILES.BBS` or `FILES.BSS`
2. `FILE_ID.DIZ` inside an archive
3. Header from `FILELIST` or `FILELIST.*`
4. Header from `README`, `README.*`, or `READ.ME`
5. `Description coming soon`

At the end of the run, files for which no real description source was found
are listed for the sysop.

## Legacy encodings

The tool can automatically detect common legacy encodings:

- UTF-8
- CP437
- CP850
- Latin-1

An encoding can also be selected explicitly:

```bash
python3 tools/bbs-files-tool.py -bd \
  --filebase /path/to/filebase \
  --file.bbs /path/to/filebase/FILES.BBS \
  --files-bbs-encoding cp437 \
  --diz-encoding cp437
```

## Archive support

ZIP and TAR-family archives are handled by Python directly. For older ZIP
compression methods and additional formats, install `7zz` or `7z`.

## Strict mode

Use `--strict` to prevent writing the cache when the index references missing
files or archive inspection reports an error:

```bash
python3 tools/bbs-files-tool.py -bd \
  --filebase /path/to/filebase \
  --file.bbs /path/to/filebase/FILES.BBS \
  --strict
```

## Help and contact

```bash
python3 tools/bbs-files-tool.py --help
```

Requests: neo67@linuxmintusers.de
