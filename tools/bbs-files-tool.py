#!/usr/bin/env python3
"""
ANetBBS file-base migration helper.

Current action:
  -bd / --build-descriptions

Builds the .descriptions.json cache used by ANetBBS.

Description priority:
1. Existing FILES.BBS / FILES.BSS entry
2. FILE_ID.DIZ found inside an archive
3. FILELIST or FILELIST.* header (first 9 lines, before the file table)
4. README / README.* / READ.ME header (first 9 lines)
5. Configurable fallback text (default: "Description coming soon")

The original FILES.BBS/FILES.BSS is never modified, renamed, hidden, or
deleted. It remains a normal part of the file area.

Examples:

  Preview the result without writing anything:

    python3 bbs-files-tool.py -bd \
      --filebase /opt/anetbbs/data/files/OS2.BBS \
      --file.bbs /opt/anetbbs/data/files/OS2.BBS/FILES.BSS \
      --dry-run

  Build /opt/anetbbs/data/files/OS2.BBS/.descriptions.json:

    sudo python3 bbs-files-tool.py -bd \
      --filebase /opt/anetbbs/data/files/OS2.BBS \
      --file.bbs /opt/anetbbs/data/files/OS2.BBS/FILES.BSS

  Use FILES.BBS instead of FILES.BSS:

    sudo python3 bbs-files-tool.py --build-descriptions \
      --filebase /opt/anetbbs/data/files/GAMES \
      --files-bbs /opt/anetbbs/data/files/GAMES/FILES.BBS

  Change the fallback text for files without any description source:

    sudo python3 bbs-files-tool.py -bd \
      --filebase /opt/anetbbs/data/files/OS2.BBS \
      --file.bbs /opt/anetbbs/data/files/OS2.BBS/FILES.BSS \
      --empty-description "Description coming soon"

Issues and feature requests: https://github.com/anetonline/ANetBBS/issues
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_OUTPUT_NAME = ".descriptions.json"
MAX_DIZ_BYTES = 256 * 1024
ARCHIVE_TIMEOUT_SECONDS = 20

# Formats Python can handle directly, plus common formats supported by 7z/7zz.
ARCHIVE_SUFFIXES = {
    ".zip",
    ".tar",
    ".tgz",
    ".tbz",
    ".tbz2",
    ".txz",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".arj",
    ".lha",
    ".lzh",
}

ANSI_ESCAPE_RE = re.compile(
    r"""
    \x1B
    (?:
        \[[0-?]*[ -/]*[@-~]     # CSI sequence
      | \][^\x07]*(?:\x07|\x1B\\)  # OSC sequence
      | [@-_]                   # two-character escape
    )
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class FileBbsEntry:
    filename: str
    description: str
    line_number: int


@dataclass(frozen=True)
class BuildStats:
    files_total: int
    from_files_bbs: int
    from_file_id_diz: int
    from_filelist: int
    from_readme: int
    without_description: int
    files_without_source_description: tuple[str, ...]
    index_entries_missing_from_filebase: tuple[str, ...]
    archive_errors: tuple[str, ...]


def decode_penalty(text: str) -> int:
    """
    Estimate how implausible a single-byte decoding looks.

    UTF-8 is tried first. For old DOS text files, CP437/CP850/Latin-1 are
    scored heuristically. C1 controls and DOS-art glyphs are strong signals
    that the wrong code page was selected.
    """
    penalty = 0

    for char in text:
        codepoint = ord(char)

        if char in "\r\n\t":
            continue

        category = unicodedata.category(char)

        if category == "Cc":
            penalty += 100
        elif 0x2500 <= codepoint <= 0x259F:
            penalty += 12
        elif 0x0370 <= codepoint <= 0x03FF:
            penalty += 6
        elif category == "Sm":
            penalty += 6
        elif category == "So":
            penalty += 3

    return penalty


def decode_legacy_text(raw: bytes, requested_encoding: str = "auto") -> tuple[str, str]:
    """Decode UTF-8 or classic DOS/Latin-1 text."""
    if requested_encoding != "auto":
        try:
            return raw.decode(requested_encoding), requested_encoding
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Could not decode text as {requested_encoding}: {exc}"
            ) from exc

    try:
        return raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        pass

    candidates: list[tuple[int, int, str, str]] = []
    preference = {"cp437": 0, "cp850": 1, "latin-1": 2}

    for encoding in ("cp437", "cp850", "latin-1"):
        text = raw.decode(encoding)
        candidates.append(
            (decode_penalty(text), preference[encoding], encoding, text)
        )

    _, _, encoding, text = min(candidates)
    return text, encoding


def parse_files_bbs(text: str) -> tuple[list[FileBbsEntry], list[str]]:
    """
    Parse a traditional FILES.BBS-compatible index.

    The first token of a non-indented line is the filename. Remaining text is
    its description. Indented lines continue the preceding description.
    """
    entries: list[FileBbsEntry] = []
    warnings: list[str] = []

    current_name: str | None = None
    current_description: list[str] = []
    current_line_number = 0

    def finish_current() -> None:
        nonlocal current_name, current_description, current_line_number

        if current_name is not None:
            entries.append(
                FileBbsEntry(
                    filename=current_name,
                    description="\n".join(current_description).strip(),
                    line_number=current_line_number,
                )
            )

        current_name = None
        current_description = []
        current_line_number = 0

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.rstrip()

        if not line.strip():
            continue

        if line[:1].isspace() and current_name is not None:
            current_description.append(line.strip())
            continue

        finish_current()

        parts = line.strip().split(maxsplit=1)
        current_name = parts[0]
        current_description = [parts[1].strip()] if len(parts) == 2 else []
        current_line_number = line_number

        if len(parts) == 1:
            warnings.append(
                f"Line {line_number}: {current_name!r} has no description."
            )

    finish_current()
    return entries, warnings


def clean_description(text: str) -> str:
    """Turn FILE_ID.DIZ text into safe, readable plain text."""
    text = text.replace("\x1a", "")
    text = ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines: list[str] = []

    for line in text.split("\n"):
        cleaned = "".join(
            char
            for char in line
            if char == "\t" or unicodedata.category(char) != "Cc"
        ).rstrip()
        cleaned_lines.append(cleaned)

    while cleaned_lines and not cleaned_lines[0].strip():
        cleaned_lines.pop(0)

    while cleaned_lines and not cleaned_lines[-1].strip():
        cleaned_lines.pop()

    return "\n".join(cleaned_lines).strip()


def classify_description_member(name: str) -> tuple[int, str] | None:
    """
    Classify a possible archive description file.

    Lower numeric values have higher priority:
      0: FILE_ID.DIZ
      1: FILELIST or FILELIST.*
      2: README, README.*, READ.ME, or variants with leading ! characters
    """
    base = Path(name.replace("\\", "/")).name
    folded = base.casefold()
    readme_folded = folded.lstrip("!")

    if folded == "file_id.diz":
        return 0, "FILE_ID.DIZ"

    if folded == "filelist" or folded.startswith("filelist."):
        return 1, "FILELIST"

    if (
        readme_folded == "readme"
        or readme_folded.startswith("readme.")
        or readme_folded == "read.me"
        or readme_folded.startswith("read.me.")
    ):
        return 2, "README"

    return None


def choose_description_member(
    names: Iterable[str],
) -> tuple[str, str] | None:
    """Choose the best description-bearing archive member."""
    name_list = list(names)
    candidates: list[tuple[int, int, int, str, str]] = []

    for name in name_list:
        classified = classify_description_member(name)
        if classified is None:
            continue

        priority, source_kind = classified
        normalized = name.replace("\\", "/")
        candidates.append(
            (
                priority,
                len(Path(normalized).parts),
                len(name),
                name.casefold(),
                source_kind,
            )
        )

    if not candidates:
        return None

    priority, depth, length, folded_name, source_kind = min(candidates)

    # Recover the original spelling for the chosen candidate.
    for name in name_list:
        classified = classify_description_member(name)
        if classified is None:
            continue
        candidate_priority, candidate_kind = classified
        normalized = name.replace("\\", "/")
        key = (
            candidate_priority,
            len(Path(normalized).parts),
            len(name),
            name.casefold(),
            candidate_kind,
        )
        if key == (
            priority,
            depth,
            length,
            folded_name,
            source_kind,
        ):
            return name, source_kind

    return None


def looks_like_filelist_entry(line: str) -> bool:
    """
    Detect the start of a FILELIST.* table.

    Typical entries look like:
        !!README.DOC    "Read-Me-First" Documentation
        PROGRAM.EXE     Executable file
    """
    return bool(
        re.match(
            r"^\s*[!#$%&'()+,\-.0-9;=@A-Z\[\]^_`a-z{}~]{1,40}"
            r"\.[A-Za-z0-9]{1,8}\s{2,}\S",
            line,
        )
    )


README_SECTION_HEADINGS = (
    "how to ",
    "installation",
    "installing",
    "usage",
    "contents",
    "requirements",
    "configuration",
    "history",
    "changes",
    "change log",
    "changelog",
    "what's new",
    "whats new",
    "upgrade",
    "upgrading",
    "getting started",
    "quick start",
    "known issues",
    "troubleshooting",
)


def looks_like_readme_section_heading(line: str) -> bool:
    """
    Detect a likely README section heading.

    The match is intentionally conservative and is only used after at least
    one description line has already been collected.
    """
    normalized = " ".join(line.strip().casefold().split())
    normalized = normalized.rstrip(":-.")

    if not normalized:
        return False

    if normalized.startswith("how to "):
        return True

    return normalized in README_SECTION_HEADINGS


def summarize_fallback_text(
    text: str,
    *,
    source_kind: str,
    max_lines: int = 9,
) -> str:
    """
    Create a concise description from FILELIST.* or README text.

    FILELIST.* headers stop before the actual filename table begins.
    README content is limited to the first max_lines physical lines after
    leading/trailing blank lines are removed.
    """
    cleaned = clean_description(text)
    if not cleaned:
        return ""

    lines = cleaned.splitlines()

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    selected: list[str] = []

    for line in lines:
        if source_kind == "FILELIST" and looks_like_filelist_entry(line):
            break

        if (
            source_kind == "README"
            and selected
            and looks_like_readme_section_heading(line)
        ):
            break

        if len(selected) >= max_lines:
            break

        # Preserve one blank separator, but avoid long runs of empty lines.
        if not line.strip() and selected and not selected[-1].strip():
            continue

        selected.append(line.rstrip())

    while selected and not selected[-1].strip():
        selected.pop()

    return "\n".join(selected).strip()


def read_description_from_zip(
    path: Path,
) -> tuple[bytes, str] | None:
    if not zipfile.is_zipfile(path):
        return None

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        chosen = choose_description_member(info.filename for info in infos)

        if chosen is None:
            return None

        member_name, source_kind = chosen
        info = archive.getinfo(member_name)

        if info.file_size > MAX_DIZ_BYTES:
            raise ValueError(
                f"{member_name} is larger than {MAX_DIZ_BYTES} bytes"
            )

        with archive.open(info) as handle:
            return handle.read(MAX_DIZ_BYTES + 1), source_kind


def read_description_from_tar(
    path: Path,
) -> tuple[bytes, str] | None:
    try:
        is_tar = tarfile.is_tarfile(path)
    except OSError:
        return None

    if not is_tar:
        return None

    with tarfile.open(path, mode="r:*") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        chosen = choose_description_member(member.name for member in members)

        if chosen is None:
            return None

        member_name, source_kind = chosen
        member = next(item for item in members if item.name == member_name)

        if member.size > MAX_DIZ_BYTES:
            raise ValueError(
                f"{member_name} is larger than {MAX_DIZ_BYTES} bytes"
            )

        handle = archive.extractfile(member)
        if handle is None:
            return None

        with handle:
            return handle.read(MAX_DIZ_BYTES + 1), source_kind


def find_7z_command() -> str | None:
    for command in ("7zz", "7z"):
        resolved = shutil.which(command)
        if resolved:
            return resolved
    return None


def list_7z_members(command: str, archive_path: Path) -> list[str]:
    result = subprocess.run(
        [command, "l", "-slt", "--", os.fspath(archive_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=ARCHIVE_TIMEOUT_SECONDS,
        check=False,
    )

    if result.returncode != 0:
        raise ValueError(
            result.stderr.strip()
            or f"{Path(command).name} returned exit status {result.returncode}"
        )

    names: list[str] = []

    for line in result.stdout.splitlines():
        if line.startswith("Path = "):
            value = line[7:].strip()

            # The first Path line is usually the archive itself.
            if value and Path(value).name != archive_path.name:
                names.append(value)

    return names


def read_7z_member(
    command: str,
    archive_path: Path,
    member_name: str,
) -> bytes:
    result = subprocess.run(
        [
            command,
            "x",
            "-so",
            "-bd",
            "-y",
            "--",
            os.fspath(archive_path),
            member_name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=ARCHIVE_TIMEOUT_SECONDS,
        check=False,
    )

    if result.returncode != 0:
        error_text = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(
            error_text
            or f"{Path(command).name} returned exit status {result.returncode}"
        )

    if len(result.stdout) > MAX_DIZ_BYTES:
        raise ValueError(
            f"FILE_ID.DIZ is larger than {MAX_DIZ_BYTES} bytes"
        )

    return result.stdout


def read_archive_description(
    path: Path,
) -> tuple[bytes, str] | None:
    """
    Read the best available description source from an archive.

    Priority:
      FILE_ID.DIZ -> FILELIST.* -> README

    ZIP and TAR-family formats are attempted with Python's standard library
    first. Old ZIP compression methods unsupported by Python automatically
    fall back to 7zz/7z when available.
    """
    zip_fallback_needed = False

    try:
        result = read_description_from_zip(path)
    except NotImplementedError:
        zip_fallback_needed = True
        result = None
    except RuntimeError:
        zip_fallback_needed = True
        result = None

    if result is not None:
        return result

    if not zip_fallback_needed:
        result = read_description_from_tar(path)
        if result is not None:
            return result

    if path.suffix.casefold() not in ARCHIVE_SUFFIXES:
        return None

    command = find_7z_command()
    if command is None:
        if zip_fallback_needed:
            raise ValueError(
                "ZIP compression method is unsupported by Python and neither "
                "7zz nor 7z is installed"
            )
        return None

    names = list_7z_members(command, path)
    chosen = choose_description_member(names)

    if chosen is None:
        return None

    member_name, source_kind = chosen
    return read_7z_member(command, path, member_name), source_kind



def load_existing_cache(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read existing cache {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(
            f"Existing cache {path} must contain a JSON object."
        )

    return data


def numbered_backup_path(path: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{timestamp}")
    counter = 1

    while candidate.exists():
        candidate = path.with_name(
            f"{path.name}.bak-{timestamp}-{counter}"
        )
        counter += 1

    return candidate


def write_json_atomic(
    output_path: Path,
    data: dict[str, object],
    *,
    owner_source: Path,
) -> Path | None:
    """Back up an existing output file, then replace it atomically."""
    backup_path: Path | None = None
    old_stat = output_path.stat() if output_path.exists() else None
    owner_stat = owner_source.stat()

    if output_path.exists():
        backup_path = numbered_backup_path(output_path)
        shutil.copy2(output_path, backup_path)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
        text=True,
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        mode = (old_stat.st_mode & 0o777) if old_stat else 0o644
        os.chmod(temp_path, mode)

        if os.geteuid() == 0:
            uid = old_stat.st_uid if old_stat else owner_stat.st_uid
            gid = old_stat.st_gid if old_stat else owner_stat.st_gid
            os.chown(temp_path, uid, gid)

        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return backup_path


def build_descriptions(
    *,
    filebase: Path,
    files_bbs_path: Path,
    files_bbs_encoding: str,
    diz_encoding: str,
    include_hidden: bool,
    empty_description: str,
) -> tuple[dict[str, object], BuildStats, list[str], str]:
    raw_index = files_bbs_path.read_bytes()
    index_text, detected_index_encoding = decode_legacy_text(
        raw_index,
        requested_encoding=files_bbs_encoding,
    )
    entries, parser_warnings = parse_files_bbs(index_text)

    index_by_name: dict[str, FileBbsEntry] = {}
    duplicate_names: list[str] = []

    for entry in entries:
        key = entry.filename.casefold()

        if key in index_by_name:
            duplicate_names.append(entry.filename)

        index_by_name[key] = entry

    if duplicate_names:
        parser_warnings.append(
            "Duplicate filenames in FILES.BBS; the last entry wins: "
            + ", ".join(sorted(set(duplicate_names), key=str.casefold))
        )

    files: list[Path] = []

    for item in filebase.iterdir():
        if not item.is_file():
            continue
        if not include_hidden and item.name.startswith("."):
            continue
        if item.name == DEFAULT_OUTPUT_NAME:
            continue
        if item.name.startswith(f"{DEFAULT_OUTPUT_NAME}.bak-"):
            continue
        files.append(item)

    files.sort(key=lambda item: item.name.casefold())

    descriptions: dict[str, object] = {}
    present_names = {item.name.casefold() for item in files}
    from_files_bbs = 0
    from_file_id_diz = 0
    from_filelist = 0
    from_readme = 0
    without_description = 0
    files_without_source_description: list[str] = []
    archive_errors: list[str] = []

    for item in files:
        stat = item.stat()
        entry = index_by_name.get(item.name.casefold())
        description = ""

        if entry is not None:
            description = entry.description
            from_files_bbs += 1
        elif item.suffix.casefold() in ARCHIVE_SUFFIXES:
            try:
                archive_description = read_archive_description(item)
            except (
                OSError,
                ValueError,
                RuntimeError,
                NotImplementedError,
                zipfile.BadZipFile,
                tarfile.TarError,
            ) as exc:
                archive_errors.append(f"{item.name}: {exc}")
                archive_description = None

            if archive_description:
                raw_text, source_kind = archive_description

                try:
                    decoded_text, _ = decode_legacy_text(
                        raw_text,
                        requested_encoding=diz_encoding,
                    )

                    if source_kind == "FILE_ID.DIZ":
                        description = clean_description(decoded_text)
                    else:
                        description = summarize_fallback_text(
                            decoded_text,
                            source_kind=source_kind,
                            max_lines=9,
                        )
                except ValueError as exc:
                    archive_errors.append(f"{item.name}: {exc}")

                if description:
                    if source_kind == "FILE_ID.DIZ":
                        from_file_id_diz += 1
                    elif source_kind == "FILELIST":
                        from_filelist += 1
                    elif source_kind == "README":
                        from_readme += 1

        if not description:
            without_description += 1
            files_without_source_description.append(item.name)
            description = empty_description

        descriptions[item.name] = {
            "description": description,
            "mtime": int(stat.st_mtime),
            "size": stat.st_size,
        }

    missing_from_filebase = tuple(
        entry.filename
        for key, entry in sorted(
            index_by_name.items(),
            key=lambda pair: pair[1].filename.casefold(),
        )
        if key not in present_names
    )

    stats = BuildStats(
        files_total=len(files),
        from_files_bbs=from_files_bbs,
        from_file_id_diz=from_file_id_diz,
        from_filelist=from_filelist,
        from_readme=from_readme,
        without_description=without_description,
        files_without_source_description=tuple(
            files_without_source_description
        ),
        index_entries_missing_from_filebase=missing_from_filebase,
        archive_errors=tuple(archive_errors),
    )

    return descriptions, stats, parser_warnings, detected_index_encoding


def print_list(title: str, values: Iterable[str]) -> None:
    items = list(values)

    if not items:
        return

    print(f"\n{title}")
    for item in items:
        print(f"  {item}")


def print_sysop_description_notice(files: Iterable[str]) -> None:
    items = list(files)

    if not items:
        print("\nAll files have a source description.")
        return

    print("\nDear Sysop, please add Description to following files:")
    for item in items:
        print(f"  {item}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bbs-files-tool.py",
        description="Utilities for ANetBBS file bases.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:

  Preview without writing:

    python3 bbs-files-tool.py -bd \\
      --filebase /opt/anetbbs/data/files/OS2.BBS \\
      --file.bbs /opt/anetbbs/data/files/OS2.BBS/FILES.BSS \\
      --dry-run

  Build the ANetBBS .descriptions.json cache:

    sudo python3 bbs-files-tool.py -bd \\
      --filebase /opt/anetbbs/data/files/OS2.BBS \\
      --file.bbs /opt/anetbbs/data/files/OS2.BBS/FILES.BSS

  Long option names are also accepted:

    sudo python3 bbs-files-tool.py --build-descriptions \\
      --filebase /opt/anetbbs/data/files/GAMES \\
      --files-bbs /opt/anetbbs/data/files/GAMES/FILES.BBS

  Issues and feature requests: https://github.com/anetonline/ANetBBS/issues
""",
    )
    parser.add_argument(
        "-bd",
        "--build-descriptions",
        action="store_true",
        help="build the ANetBBS .descriptions.json cache",
    )
    parser.add_argument(
        "--filebase",
        type=Path,
        help="path to the ANetBBS file-area directory",
    )
    parser.add_argument(
        "--file.bbs",
        "--files-bbs",
        dest="files_bbs",
        type=Path,
        help="path to FILES.BBS or FILES.BSS",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=(
            "output path; default: FILEBASE/.descriptions.json "
            "(the filename ANetBBS expects)"
        ),
    )
    parser.add_argument(
        "--files-bbs-encoding",
        choices=("auto", "utf-8-sig", "cp437", "cp850", "latin-1"),
        default="auto",
        help="encoding of FILES.BBS; default: auto",
    )
    parser.add_argument(
        "--diz-encoding",
        choices=("auto", "utf-8-sig", "cp437", "cp850", "latin-1"),
        default="auto",
        help="encoding of FILE_ID.DIZ files; default: auto",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="also add hidden regular files from the filebase",
    )
    parser.add_argument(
        "--empty-description",
        default="Description coming soon",
        help=(
            "text used when no FILES.BBS, FILE_ID.DIZ, FILELIST.*, or README "
            "description is available; default: %(default)r"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the planned result without writing JSON",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "do not write output when FILES.BBS references missing files "
            "or an archive scan fails"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.build_descriptions:
        parser.error("No action selected. Use -bd / --build-descriptions.")

    if args.filebase is None:
        parser.error("-bd requires --filebase.")

    if args.files_bbs is None:
        parser.error("-bd requires --file.bbs or --files-bbs.")

    filebase = args.filebase.expanduser().resolve()
    files_bbs_path = args.files_bbs.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else filebase / DEFAULT_OUTPUT_NAME
    )

    if not filebase.is_dir():
        parser.error(f"Filebase directory not found: {filebase}")

    if not files_bbs_path.is_file():
        parser.error(f"FILES.BBS file not found: {files_bbs_path}")

    if output_path.parent != filebase:
        print(
            "Warning: output is outside the filebase. "
            "ANetBBS normally expects FILEBASE/.descriptions.json.",
            file=sys.stderr,
        )

    try:
        descriptions, stats, parser_warnings, detected_encoding = (
            build_descriptions(
                filebase=filebase,
                files_bbs_path=files_bbs_path,
                files_bbs_encoding=args.files_bbs_encoding,
                diz_encoding=args.diz_encoding,
                include_hidden=args.include_hidden,
                empty_description=args.empty_description,
            )
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Filebase:                    {filebase}")
    print(f"FILES.BBS:                   {files_bbs_path}")
    print(f"FILES.BBS encoding:          {detected_encoding}")
    print(f"Output:                      {output_path}")
    print(f"Visible files processed:     {stats.files_total}")
    print(f"Descriptions from FILES.BBS:   {stats.from_files_bbs}")
    print(f"Descriptions from FILE_ID.DIZ: {stats.from_file_id_diz}")
    print(f"Descriptions from FILELIST.*:  {stats.from_filelist}")
    print(f"Descriptions from README:      {stats.from_readme}")
    print(f"Files without source description: {stats.without_description}")
    print(f"Fallback description:          {args.empty_description!r}")
    print(
        "FILES.BBS entries missing from filebase: "
        f"{len(stats.index_entries_missing_from_filebase)}"
    )
    print(f"Archive scan errors:         {len(stats.archive_errors)}")

    print_list("Parser warnings:", parser_warnings)
    print_list(
        "FILES.BBS entries missing from the filebase:",
        stats.index_entries_missing_from_filebase,
    )
    print_list("Archive scan errors:", stats.archive_errors)

    has_strict_errors = bool(
        stats.index_entries_missing_from_filebase
        or stats.archive_errors
    )

    if args.strict and has_strict_errors:
        print(
            "\nStrict mode: output was not written because problems were found.",
            file=sys.stderr,
        )
        print_sysop_description_notice(
            stats.files_without_source_description
        )
        return 2

    if args.dry_run:
        print("\nDry run: no file was written.")
        print_sysop_description_notice(
            stats.files_without_source_description
        )
        return 0

    try:
        backup_path = write_json_atomic(
            output_path,
            descriptions,
            owner_source=filebase,
        )
    except OSError as exc:
        print(f"Error writing {output_path}: {exc}", file=sys.stderr)
        return 1

    if backup_path is not None:
        print(f"\nBackup: {backup_path}")

    print(f"Written: {output_path}")
    print("The original FILES.BBS/FILES.BSS was not modified.")
    print("Refresh the ANetBBS file-area page; no service restart is required.")
    print_sysop_description_notice(
        stats.files_without_source_description
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
