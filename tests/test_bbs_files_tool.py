#!/usr/bin/env python3
"""Regression tests for tools/bbs-files-tool.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "bbs-files-tool.py"


class BbsFilesToolTests(unittest.TestCase):
    def run_tool(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOOL), *arguments],
            check=False,
            text=True,
            capture_output=True,
        )

    def test_builds_descriptions_from_all_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            filebase = Path(temporary_directory) / "OS2.BBS"
            filebase.mkdir()

            files_bbs = filebase / "FILES.BSS"
            original_index = (
                "INDEXED.ZIP Description from FILES.BSS\n"
            )
            files_bbs.write_text(original_index, encoding="utf-8")

            with zipfile.ZipFile(filebase / "INDEXED.ZIP", "w") as archive:
                archive.writestr(
                    "FILE_ID.DIZ",
                    "This must not replace the FILES.BSS description.",
                )

            with zipfile.ZipFile(filebase / "DIZ.ZIP", "w") as archive:
                archive.writestr(
                    "docs/FILE_ID.DIZ",
                    "Grüße aus Köln\r\nSecond line\r\n".encode("cp437"),
                )

            with zipfile.ZipFile(filebase / "FILELIST.ZIP", "w") as archive:
                archive.writestr(
                    "FILELIST.212",
                    (
                        "\r\n"
                        "The Blue Wave Offline Mail Reader\r\n"
                        "v2.12 OS/2\r\n"
                        "\r\n"
                        "List of All Included Files\r\n"
                        "Copyright (C) 1994 by Cutting Edge Computing\r\n"
                        "All Rights Reserved.\r\n"
                        "\r\n"
                        "!!README.DOC    Read-Me-First Documentation\r\n"
                        "PROGRAM.EXE     Executable file\r\n"
                    ),
                )

            with zipfile.ZipFile(filebase / "README.ZIP", "w") as archive:
                archive.writestr(
                    "README",
                    (
                        "\r\n"
                        "Lora Bulletin Board System and\r\n"
                        "Electronic Mail Interface\r\n"
                        "\r\n"
                        "Version 2.40 (beta 4)\r\n"
                        "\r\n"
                        "Copyright (c) 1989-1994 by Marco Maccaferri\r\n"
                        "All rights reserved\r\n"
                        "\r\n"
                        "How to upgrade\r\n"
                        "This section must not appear.\r\n"
                    ),
                )

            (filebase / "UNKNOWN.BIN").write_bytes(b"unknown")

            result = self.run_tool(
                "-bd",
                "--filebase",
                str(filebase),
                "--file.bbs",
                str(files_bbs),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                files_bbs.read_text(encoding="utf-8"),
                original_index,
            )

            output = filebase / ".descriptions.json"
            self.assertTrue(output.is_file())

            descriptions = json.loads(output.read_text(encoding="utf-8"))

            self.assertEqual(
                descriptions["INDEXED.ZIP"]["description"],
                "Description from FILES.BSS",
            )
            self.assertEqual(
                descriptions["DIZ.ZIP"]["description"],
                "Grüße aus Köln\nSecond line",
            )

            filelist_description = descriptions["FILELIST.ZIP"]["description"]
            self.assertIn(
                "The Blue Wave Offline Mail Reader",
                filelist_description,
            )
            self.assertNotIn("!!README.DOC", filelist_description)

            readme_description = descriptions["README.ZIP"]["description"]
            self.assertIn(
                "Lora Bulletin Board System and",
                readme_description,
            )
            self.assertIn("All rights reserved", readme_description)
            self.assertNotIn("How to upgrade", readme_description)

            self.assertEqual(
                descriptions["UNKNOWN.BIN"]["description"],
                "Description coming soon",
            )
            self.assertIn(
                "Dear Sysop, please add Description to following files:",
                result.stdout,
            )
            self.assertIn("UNKNOWN.BIN", result.stdout)

    def test_dry_run_does_not_write_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            filebase = Path(temporary_directory) / "FILES"
            filebase.mkdir()

            files_bbs = filebase / "FILES.BBS"
            files_bbs.write_text(
                "PACKAGE.ZIP Test package\n",
                encoding="utf-8",
            )
            (filebase / "PACKAGE.ZIP").write_bytes(b"not-an-archive")

            result = self.run_tool(
                "-bd",
                "--filebase",
                str(filebase),
                "--file.bbs",
                str(files_bbs),
                "--dry-run",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((filebase / ".descriptions.json").exists())
            self.assertIn("Dry run: no file was written.", result.stdout)

    def test_existing_output_is_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            filebase = Path(temporary_directory) / "FILES"
            filebase.mkdir()

            files_bbs = filebase / "FILES.BBS"
            files_bbs.write_text(
                "PACKAGE.ZIP Test package\n",
                encoding="utf-8",
            )
            (filebase / "PACKAGE.ZIP").write_bytes(b"test")

            first = self.run_tool(
                "-bd",
                "--filebase",
                str(filebase),
                "--file.bbs",
                str(files_bbs),
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            second = self.run_tool(
                "-bd",
                "--filebase",
                str(filebase),
                "--file.bbs",
                str(files_bbs),
            )
            self.assertEqual(second.returncode, 0, second.stderr)

            backups = list(
                filebase.glob(".descriptions.json.bak-*")
            )
            self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
