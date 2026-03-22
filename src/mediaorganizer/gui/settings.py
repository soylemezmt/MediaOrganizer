from dataclasses import dataclass, field

@dataclass
class ColumnSettings:
    show_type: bool = False
    show_metadata: bool = True
    show_filename: bool = True
    show_folder: bool = True
    show_filesystem: bool = True
    show_size: bool = True
    show_path: bool = True
    show_country: bool = False
    show_city: bool = False

@dataclass
class DateSourceSettings:
    metadata_tag: str = "DateTimeOriginal"
    filesystem_time: str = "ctime"   # ctime / mtime

@dataclass
class UiOptions:
    columns: ColumnSettings = field(default_factory=ColumnSettings)
    date_sources: DateSourceSettings = field(default_factory=DateSourceSettings)