# Photo Organizer

This project organizes photo and video files into `YYYY/MM` folders based on date information.

## Features

- Recursively scans source folders
- Determines date using configurable priority:
  - metadata
  - filename
  - folder path
  - filesystem date
- Copies files without changing original names
- Skips duplicates if name and size are the same
- Renames on collision with `_01`, `_02`, etc.
- Supports dry-run mode
- Writes CSV logs

## Requirements

- Python 3.10+
- ExifTool installed and available in PATH

## Usage

```bash
python src/organize_media.py "D:\Source" "E:\Target" --dry-run

python src/organize_media.py "D:\Source" "E:\Target" --priority "filename,metadata,folder,filesystem"

folder,filename,metadata,filesystem