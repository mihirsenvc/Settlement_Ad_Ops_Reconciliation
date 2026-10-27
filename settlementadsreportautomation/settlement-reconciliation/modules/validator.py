"""Input parsing and schema validation for uploaded financial reports."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol
from zipfile import BadZipFile, ZipFile

import numpy as np
import pandas as pd

from utils.constants import REQUIRED_COLUMNS

ReportRole = Literal["settlement", "wallet", "campaign"]


class SeekableBinaryStream(Protocol):
    """Minimal stream interface used for XLSX archive preflight validation."""

    def read(self, size: int = -1, /) -> bytes: ...

    def seek(self, pos: int, whence: int = 0, /) -> int: ...

    def tell(self) -> int: ...


class FileValidationError(ValueError):
    """Raised when an uploaded report is unreadable or violates its schema."""


def _canonical_column(value: object) -> str:
    """Normalize a column label for case- and punctuation-insensitive matching."""
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def validate_report(
    filename: str,
    content: bytes | bytearray | memoryview[int],
    role: ReportRole,
    max_xlsx_expanded_size_bytes: int | None = None,
    file_stream: SeekableBinaryStream | None = None,
) -> None:
    """Validate filename extension and ensure upload content is non-empty."""
    extension = Path(filename).suffix.lower()
    if extension not in {".csv", ".xlsx"}:
        raise FileValidationError(f"{filename}: only .csv and .xlsx files are supported.")
    if not content:
        raise FileValidationError(f"{filename}: the uploaded file is empty.")
    if role not in REQUIRED_COLUMNS:
        raise FileValidationError(f"Unsupported report role: {role}.")
    if (
        extension == ".xlsx"
        and max_xlsx_expanded_size_bytes is not None
        and max_xlsx_expanded_size_bytes < 1
    ):
        raise ValueError("max_xlsx_expanded_size_bytes must be positive.")
    if extension == ".xlsx" and max_xlsx_expanded_size_bytes is not None:
        archive_stream = (
            file_stream if file_stream is not None else BytesIO(content)
        )
        try:
            archive_stream.seek(0)
            with ZipFile(archive_stream) as archive:
                expanded_size = sum(member.file_size for member in archive.infolist())
            if expanded_size > max_xlsx_expanded_size_bytes:
                limit_mb = max_xlsx_expanded_size_bytes / (1024 * 1024)
                raise FileValidationError(
                    f"{filename}: XLSX expands beyond the configured {limit_mb:.0f} MB limit."
                )
        except FileValidationError:
            raise
        except (BadZipFile, OSError, ValueError, EOFError) as exc:
            raise FileValidationError(f"{filename}: invalid XLSX archive.") from exc
        finally:
            archive_stream.seek(0)


def load_report(
    file_obj: SeekableBinaryStream,
    filename: str,
    role: ReportRole,
) -> pd.DataFrame:
    """Parse a CSV/XLSX report, normalize known headers, and validate its columns."""
    try:
        file_obj.seek(0)
        if Path(filename).suffix.lower() == ".csv":
            frame = pd.read_csv(
                file_obj,
                dtype="string",
                keep_default_na=False,
            )
        elif Path(filename).suffix.lower() == ".xlsx":
            frame = pd.read_excel(
                file_obj,
                dtype="string",
                engine="openpyxl",
                keep_default_na=False,
            )
        else:
            raise FileValidationError(f"{filename}: only .csv and .xlsx files are supported.")
    except FileValidationError:
        raise
    except (OSError, ValueError, BadZipFile, pd.errors.ParserError, ImportError) as exc:
        raise FileValidationError(f"{filename}: unable to parse report: {exc}") from exc

    canonical_to_expected = {
        _canonical_column(column): column for column in REQUIRED_COLUMNS[role]
    }
    rename_map = {
        column: canonical_to_expected.get(_canonical_column(column), column)
        for column in frame.columns
    }
    frame = frame.rename(columns=rename_map)
    duplicated_columns = frame.columns[frame.columns.duplicated()].tolist()
    if duplicated_columns:
        raise FileValidationError(
            f"{filename}: duplicate column(s) after header normalization: "
            f"{', '.join(map(str, duplicated_columns))}."
        )
    missing = [column for column in REQUIRED_COLUMNS[role] if column not in frame.columns]
    if missing:
        raise FileValidationError(
            f"{filename}: missing required column(s): {', '.join(missing)}."
        )
    if frame.empty:
        raise FileValidationError(f"{filename}: report contains no data rows.")

    frame = frame.loc[:, REQUIRED_COLUMNS[role]]
    if role in {"settlement", "wallet"}:
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.normalize()
        invalid_dates = int(frame["Date"].isna().sum())
        if invalid_dates:
            raise FileValidationError(
                f"{filename}: {invalid_dates} row(s) have invalid or blank dates."
            )
        amount = pd.to_numeric(frame["Amount"], errors="coerce")
        invalid_amounts = amount.isna() | ~np.isfinite(amount)
        if invalid_amounts.any():
            raise FileValidationError(
                f"{filename}: Amount contains {int(invalid_amounts.sum())} blank, non-numeric, "
                "or non-finite value(s)."
            )
        frame["Amount"] = amount.astype("float64")
        for column in ("Campaign_ID", "Transaction_ID", "Customer_ID"):
            frame[column] = frame[column].astype("string").str.strip()
            frame.loc[frame[column] == "", column] = pd.NA
    else:
        for column in ("Campaign_ID", "Campaign_Name", "Business_Unit"):
            frame[column] = frame[column].astype("string").str.strip()
            frame.loc[frame[column] == "", column] = pd.NA
    return frame


def validate_campaign_mapping(frame: pd.DataFrame) -> None:
    """Reject empty, missing-key, or ambiguous campaign mappings."""
    missing = [column for column in REQUIRED_COLUMNS["campaign"] if column not in frame.columns]
    if missing:
        raise FileValidationError(f"Campaign mapping is missing: {', '.join(missing)}.")
    if frame["Campaign_ID"].isna().any():
        raise FileValidationError("Campaign mapping contains blank Campaign_ID values.")
    duplicated = frame["Campaign_ID"].duplicated(keep=False)
    if duplicated.any():
        examples = frame.loc[duplicated, "Campaign_ID"].drop_duplicates().head(5).tolist()
        raise FileValidationError(f"Campaign_ID must be unique in mapping; duplicates include {examples}.")
