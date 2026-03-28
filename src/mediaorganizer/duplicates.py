from __future__ import annotations

import filecmp
import hashlib
import io
from PIL import Image, ImageOps, UnidentifiedImageError
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .config import SUPPORTED_EXTENSIONS, SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_VIDEO_EXTENSIONS


IMAGE_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS
VIDEO_EXTENSIONS = SUPPORTED_VIDEO_EXTENSIONS


@dataclass
class DuplicateDecision:
    action: str
    target_path: Optional[Path] = None
    duplicate_paths: Optional[list[Path]] = None
    best_path: Optional[Path] = None
    reason: str = ""

def _load_normalized_image(path: Path) -> Optional[Image.Image]:
    try:
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return None

        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)

            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGB")

            return img.copy()
    except Exception:
        return None

def _average_hash(img: Image.Image, hash_size: int = 8) -> int:
    small = img.convert("L").resize((hash_size, hash_size), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)

    bits = 0
    for px in pixels:
        bits = (bits << 1) | (1 if px >= avg else 0)

    return bits


def _hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _similarity_threshold_to_max_distance(threshold: str) -> int:
    threshold = (threshold or "high").lower()

    if threshold == "high":
        return 4
    if threshold == "medium":
        return 8
    if threshold == "low":
        return 12

    return 4

def _are_duplicates_image_exact(path1: Path, path2: Path) -> bool:
    img1 = _load_normalized_image(path1)
    img2 = _load_normalized_image(path2)

    if img1 is None or img2 is None:
        return False

    try:
        if img1.size != img2.size:
            return False

        if img1.mode != img2.mode:
            common_mode = "RGBA" if "A" in (img1.mode + img2.mode) else "RGB"
            img1 = img1.convert(common_mode)
            img2 = img2.convert(common_mode)

        return img1.tobytes() == img2.tobytes()
    except Exception:
        return False
    finally:
        try:
            img1.close()
        except Exception:
            pass
        try:
            img2.close()
        except Exception:
            pass



def _are_duplicates_image_similar(path1: Path, path2: Path, threshold: str = "high") -> bool:
    img1 = _load_normalized_image(path1)
    img2 = _load_normalized_image(path2)

    if img1 is None or img2 is None:
        return False

    try:
        w1, h1 = img1.size
        w2, h2 = img2.size

        if w1 == 0 or h1 == 0 or w2 == 0 or h2 == 0:
            return False

        ratio1 = w1 / h1
        ratio2 = w2 / h2

        if abs(ratio1 - ratio2) > 0.15:
            return False

        hash1 = _average_hash(img1, hash_size=8)
        hash2 = _average_hash(img2, hash_size=8)

        dist = _hamming_distance(hash1, hash2)
        max_dist = _similarity_threshold_to_max_distance(threshold)

        return dist <= max_dist
    except Exception:
        return False
    finally:
        try:
            img1.close()
        except Exception:
            pass
        try:
            img2.close()
        except Exception:
            pass

def is_supported_for_duplicate_policy(path: Path, file_types: str) -> bool:
    suffix = path.suffix.lower()

    if suffix not in SUPPORTED_EXTENSIONS:
        return False

    if file_types == "images_only":
        return suffix in IMAGE_EXTENSIONS

    if file_types == "images_and_videos":
        return suffix in IMAGE_EXTENSIONS or suffix in VIDEO_EXTENSIONS

    return False


def duplicate_scope_applies(options, operation: str, path: Path) -> bool:
    if not is_supported_for_duplicate_policy(path, options.scope.file_types):
        return False

    if operation == "copy":
        return bool(options.scope.apply_on_copy)

    if operation == "move":
        return bool(options.scope.apply_on_move)

    if operation == "rename":
        return bool(options.scope.apply_on_rename)

    return False


def iter_candidate_files(dest_dir: Path, incoming_path: Path) -> Iterable[Path]:
    if not dest_dir.exists() or not dest_dir.is_dir():
        return []

    try:
        return [
            p for p in dest_dir.iterdir()
            if p.is_file() and p.resolve() != incoming_path.resolve()
        ]
    except Exception:
        return []


def find_duplicate_candidates(
    dest_dir: Path,
    incoming_path: Path,
    options,
) -> list[Path]:
    """
    Hedef klasördeki olası duplicate adaylarını döndürür.
    İlk aşamada kaba ön eleme yapar:
    - yalnızca desteklenen dosya tipleri
    - images_only ise sadece resimler
    - uzantı eşleşmesi tercih edilir
    - method=name_size ise aynı boyut ön elemesi yapılır
    """
    if not is_supported_for_duplicate_policy(incoming_path, options.scope.file_types):
        return []

    incoming_suffix = incoming_path.suffix.lower()
    incoming_size = _safe_size(incoming_path)
    method = options.detection.method

    candidates: list[Path] = []
    for p in iter_candidate_files(dest_dir, incoming_path):
        try:
            if not is_supported_for_duplicate_policy(p, options.scope.file_types):
                continue

            # İlk aşamada uzantı eşleşmesini korumak daha güvenli.
            if p.suffix.lower() != incoming_suffix:
                continue

            if method == "name_size":
                if _safe_size(p) != incoming_size:
                    continue
                
            if method == "image_exact":
                if _safe_size(p) != incoming_size:
                    continue
                
            if method in {"image_exact", "image_similar"}:
                if p.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue

            candidates.append(p)
        except Exception:
            continue

    return candidates


def are_duplicates(path1: Path, path2: Path, detection_options) -> bool:
    method = detection_options.method

    if method == "name_size":
        return _are_duplicates_name_size(path1, path2)

    if method == "binary_exact":
        return _are_duplicates_binary(path1, path2)

    if method == "image_exact":
        return _are_duplicates_image_exact(path1, path2)

    if method == "image_similar":
        return _are_duplicates_image_similar(
            path1,
            path2,
            threshold=detection_options.similarity_threshold,
        )

    return False


def find_actual_duplicates(
    dest_dir: Path,
    incoming_path: Path,
    options,
) -> list[Path]:
    candidates = find_duplicate_candidates(dest_dir, incoming_path, options)
    result: list[Path] = []

    for candidate in candidates:
        try:
            if are_duplicates(incoming_path, candidate, options.detection):
                result.append(candidate)
        except Exception:
            continue

    return result


def choose_best_version(paths: list[Path], rule: str) -> Optional[Path]:
    if not paths:
        return None

    if len(paths) == 1:
        return paths[0]

    if rule == "largest_file_size":
        return max(paths, key=lambda p: (_safe_size(p), str(p).lower()))

    if rule == "prefer_existing":
        return paths[0]

    if rule == "prefer_incoming":
        return paths[-1]

    # default: highest_resolution
    # İlk aşamada PIL bağımlılığı olmadan basit fallback:
    # resimler için çözünürlük okunabilirse onu kullan, yoksa file size'a düş.
    scored: list[tuple[int, int, str, Path]] = []
    for p in paths:
        w, h = _safe_image_resolution(p)
        scored.append((w * h, _safe_size(p), str(p).lower(), p))

    scored.sort(reverse=True)
    return scored[0][3]


def build_duplicate_renamed_path(dest_dir: Path, preferred_name: str) -> Path:
    """
    Duplicate policy için kullanıcı dostu isim üretir:
    photo.jpg -> photo (1).jpg, photo (2).jpg
    """
    base = Path(preferred_name).stem
    ext = Path(preferred_name).suffix

    candidate = dest_dir / preferred_name
    if not candidate.exists():
        return candidate

    index = 1
    while True:
        new_name = f"{base} ({index}){ext}"
        p = dest_dir / new_name
        if not p.exists():
            return p
        index += 1


def resolve_duplicate_for_destination(
    source_path: Path,
    dest_dir: Path,
    preferred_name: str,
    options,
) -> Optional[DuplicateDecision]:
    """
    Duplicate policy açısından karar üretir.
    Burada GUI popup gösterilmez; sadece karar verilir.
    'ask' davranışı için action='ask' döndürülür, GUI bunu işler.
    Duplicate bulunmazsa None döner.
    """
    actual_duplicates = find_actual_duplicates(dest_dir, source_path, options)
    if not actual_duplicates:
        return None

    action = options.action.action
    incoming_target = dest_dir / preferred_name

    if action == "skip":
        return DuplicateDecision(
            action="skip",
            duplicate_paths=actual_duplicates,
            reason="duplicate_found_skip_incoming",
        )

    if action == "rename":
        renamed_target = build_duplicate_renamed_path(dest_dir, preferred_name)
        return DuplicateDecision(
            action="rename_copy_or_move",
            target_path=renamed_target,
            duplicate_paths=actual_duplicates,
            reason="duplicate_found_keep_both",
        )

    if action == "keep_best":
        all_versions = list(actual_duplicates) + [source_path]
        best = choose_best_version(all_versions, options.action.best_version_rule)

        if best is None:
            return DuplicateDecision(
                action="skip",
                duplicate_paths=actual_duplicates,
                reason="duplicate_found_no_best_version",
            )

        if best.resolve() == source_path.resolve():
            return DuplicateDecision(
                action="replace_with_incoming",
                target_path=incoming_target,
                duplicate_paths=actual_duplicates,
                best_path=best,
                reason="incoming_is_best_version",
            )

        return DuplicateDecision(
            action="keep_existing_best",
            duplicate_paths=actual_duplicates,
            best_path=best,
            reason="existing_version_is_best",
        )

    if action == "ask":
        return DuplicateDecision(
            action="ask",
            target_path=incoming_target,
            duplicate_paths=actual_duplicates,
            reason="duplicate_found_ask_user",
        )

    return None


# -------------------------------------------------------------------
# Internal helpers
# -------------------------------------------------------------------

def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return -1


def _are_duplicates_name_size(path1: Path, path2: Path) -> bool:
    try:
        return (
            path1.name.lower() == path2.name.lower()
            and _safe_size(path1) == _safe_size(path2)
        )
    except Exception:
        return False


def _are_duplicates_binary(path1: Path, path2: Path) -> bool:
    try:
        size1 = _safe_size(path1)
        size2 = _safe_size(path2)
        if size1 < 0 or size2 < 0 or size1 != size2:
            return False

        # shallow=False => gerçek içerik karşılaştırması
        return filecmp.cmp(path1, path2, shallow=False)
    except Exception:
        return False


def _are_duplicates_image_exact(path1: Path, path2: Path) -> bool:
    """
    İlk sürüm için güvenli fallback:
    Henüz PIL tabanlı piksel karşılaştırma bağlamadıysan binary'e düş.
    Sonraki adımda gerçek decode edilmiş piksel karşılaştırması eklenebilir.
    """
    return _are_duplicates_binary(path1, path2)


def _are_duplicates_image_similar(path1: Path, path2: Path, threshold: str = "high") -> bool:
    """
    İlk sürüm için conservative fallback:
    Henüz perceptual hash / thumbnail similarity eklenmediyse image_exact'e düş.
    Böylece yanlış pozitif üretmez.
    """
    return _are_duplicates_image_exact(path1, path2)


def _safe_image_resolution(path: Path) -> tuple[int, int]:
    try:
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return (0, 0)

        from PIL import Image

        with Image.open(path) as img:
            return img.size
    except Exception:
        return (0, 0)