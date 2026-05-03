from __future__ import annotations

from pathlib import Path

import pytest

from anki_deck_generator.config.source_sets import (
    GoogleDriveSource,
    SourceSet,
    load_source_sets_yaml,
    source_set_to_jsonable,
)


def test_load_google_drive_source(tmp_path: Path) -> None:
    tok = tmp_path / "tok.json"
    tok.write_text("{}", encoding="utf-8")
    yml = tmp_path / "sources.yaml"
    yml.write_text(
        f"""
schema_version: 1
source_sets:
  lessons:
    sources:
      - provider: google-drive
        folder_ids:
          - folderABC
        file_ids:
          - fileXYZ
        credentials_file: {tok}
        external_id: my-lessons
""",
        encoding="utf-8",
    )
    cfg = load_source_sets_yaml(yml)
    ss = cfg["lessons"]
    assert isinstance(ss, SourceSet)
    assert len(ss.sources) == 1
    src = ss.sources[0]
    assert isinstance(src, GoogleDriveSource)
    assert src.folder_ids == ("folderABC",)
    assert src.file_ids == ("fileXYZ",)
    assert src.credentials_file == tok.resolve()
    assert src.external_id == "my-lessons"


def test_google_drive_requires_credentials_file(tmp_path: Path) -> None:
    yml = tmp_path / "sources.yaml"
    yml.write_text(
        """
schema_version: 1
source_sets:
  lessons:
    sources:
      - provider: google-drive
        folder_ids: ["x"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="credentials_file"):
        load_source_sets_yaml(yml)


def test_google_drive_requires_folder_or_files(tmp_path: Path) -> None:
    tok = tmp_path / "tok.json"
    tok.write_text("{}", encoding="utf-8")
    yml = tmp_path / "sources.yaml"
    yml.write_text(
        f"""
schema_version: 1
source_sets:
  lessons:
    sources:
      - provider: google-drive
        folder_ids: []
        file_ids: []
        credentials_file: {tok}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="folder_ids"):
        load_source_sets_yaml(yml)


def test_source_set_to_jsonable_google_drive(tmp_path: Path) -> None:
    tok = tmp_path / "cred.json"
    ss = SourceSet(
        name="n",
        sources=(
            GoogleDriveSource(
                provider="google-drive",
                folder_ids=("f1",),
                file_ids=(),
                credentials_file=tok,
                external_id="e",
            ),
        ),
    )
    j = source_set_to_jsonable({"n": ss})
    assert j["n"]["sources"][0]["provider"] == "google-drive"
    assert j["n"]["sources"][0]["folder_ids"] == ["f1"]
