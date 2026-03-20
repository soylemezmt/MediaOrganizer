@echo off
::.venv\Scripts\activate
::python src\organize_media.py "D:\Resimler\2001" "E:\Resimler" --verbose --priority "folder,filename,metadata,filesystem" --dry-run

python src\organize.py "D:\Resimler\Samsung\2019-01-20 - 2019-01-20" "E:\Resimler" --priority "folder,filename,metadata,filesystem" --dry-run

