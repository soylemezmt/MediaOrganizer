import subprocess
from pathlib import Path
from typing import Iterable, Union

PathLike = Union[str, Path]


def exiftool_base_cmd(*args: str) -> list[str]:
    return [
        "exiftool",
        "-charset", "filename=cp1254",
        *args,
    ]


def exiftool_run(
    args: Iterable[str],
    *,
    check: bool = False,
    text: bool = True,
):
    cmd = exiftool_base_cmd(*list(args))
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="cp1254" if text else None,
        errors="replace" if text else None,
        check=check,
    )


def exiftool_run_with_files(
    args: Iterable[str],
    files: Iterable[PathLike],
    *,
    check: bool = False,
    text: bool = True,
):
    file_args = [str(Path(f)) for f in files]
    cmd = exiftool_base_cmd(*list(args), *file_args)
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="cp1254" if text else None,
        errors="replace" if text else None,
        check=check,
    )