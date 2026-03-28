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
    show_full_path: bool = False
    show_country: bool = False
    show_city: bool = False

@dataclass
class DateSourceSettings:
    metadata_tag: str = "DateTimeOriginal"
    filesystem_time: str = "ctime"   # ctime / mtime


# ---------------------------
# Duplicate file settings
# ---------------------------

@dataclass
class DuplicateDetectionSettings:
    # name_size / binary_exact / image_exact / image_similar
    method: str = "name_size"

    # high / medium / low
    similarity_threshold: str = "high"


@dataclass
class DuplicateActionSettings:
    # rename / skip / keep_best / ask
    action: str = "rename"

    # highest_resolution / largest_file_size / prefer_existing / prefer_incoming
    best_version_rule: str = "highest_resolution"


@dataclass
class DuplicateScopeSettings:
    apply_on_copy: bool = True
    apply_on_move: bool = False
    apply_on_rename: bool = False

    # images_only / images_and_videos
    file_types: str = "images_only"


@dataclass
class DuplicateOptions:
    detection: DuplicateDetectionSettings = field(default_factory=DuplicateDetectionSettings)
    action: DuplicateActionSettings = field(default_factory=DuplicateActionSettings)
    scope: DuplicateScopeSettings = field(default_factory=DuplicateScopeSettings)


@dataclass
class UiOptions:
    columns: ColumnSettings = field(default_factory=ColumnSettings)
    date_sources: DateSourceSettings = field(default_factory=DateSourceSettings)
    duplicate_files: DuplicateOptions = field(default_factory=DuplicateOptions)