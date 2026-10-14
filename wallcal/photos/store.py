"""Uploaded photos and photo-mode settings, kept in data/photos/."""

from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

# Uploads are shrunk to this (longest side) before saving: enough to crop a full
# 1600x1200 panel in either orientation, and quick to redraw on a Pi Zero.
MAX_SIDE = 2000
THUMB_SIDE = 360
FITS = ["fill", "fit"]  # fill: crop to cover the panel; fit: show it all, with white bars
BOOSTS = ["natural", "vivid", "extra"]  # how much to punch up color before dithering (see screen.py)


@dataclass
class Photo:
    id: str
    name: str
    added: str
    selected: bool = True  # in the rotation


@dataclass
class PhotoSettings:
    fit: str = "fill"
    boost: str = "vivid"
    interval_minutes: int = 60  # change photo this often; 0 = only when asked

    def validate(self) -> None:
        if self.fit not in FITS:
            raise ValueError(f"fit must be one of {FITS}")
        if self.boost not in BOOSTS:
            raise ValueError(f"boost must be one of {BOOSTS}")
        if not 0 <= self.interval_minutes <= 7 * 24 * 60:
            raise ValueError("interval_minutes must be between 0 and 10080")


@dataclass
class _Index:
    photos: list[Photo] = field(default_factory=list)
    settings: PhotoSettings = field(default_factory=PhotoSettings)


class PhotoStore:
    def __init__(self, root: Path):
        self.root = root
        self._index_file = root / "photos.json"
        self._lock = threading.Lock()
        self._index = self._load()

    # ------------------------------------------------------------------ reading

    def photos(self) -> list[Photo]:
        with self._lock:
            return [Photo(**asdict(p)) for p in self._index.photos]

    def settings(self) -> PhotoSettings:
        with self._lock:
            return PhotoSettings(**asdict(self._index.settings))

    def image_path(self, photo_id: str) -> Path:
        return self.root / f"{_safe(photo_id)}.jpg"

    def thumb_path(self, photo_id: str) -> Path:
        return self.root / f"{_safe(photo_id)}.thumb.jpg"

    def open(self, photo_id: str) -> Image.Image:
        with Image.open(self.image_path(photo_id)) as img:
            return img.convert("RGB")

    # ------------------------------------------------------------------ writing

    def add(self, stream: BinaryIO, filename: str) -> Photo:
        """Save an uploaded image (any format Pillow reads). Raises ValueError if it isn't one."""
        try:
            img = Image.open(stream)
            img.draft("RGB", (MAX_SIDE, MAX_SIDE))  # JPEGs: decode at reduced size, much faster
            img = ImageOps.exif_transpose(img).convert("RGB")
        except (OSError, SyntaxError, Image.DecompressionBombError) as e:
            raise ValueError(f"{filename or 'file'}: not an image Pillow can read ({e})") from None
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        photo = Photo(id=secrets.token_hex(6), name=Path(filename or "photo").stem[:80],
                      added=datetime.now().isoformat(timespec="seconds"))
        self.root.mkdir(parents=True, exist_ok=True)
        img.save(self.image_path(photo.id), "JPEG", quality=92)
        img.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
        img.save(self.thumb_path(photo.id), "JPEG", quality=85)
        with self._lock:
            self._index.photos.append(photo)
            self._save()
        log.info("Added photo %s (%s)", photo.id, photo.name)
        return photo

    def set_selected(self, photo_id: str, selected: bool) -> None:
        with self._lock:
            self._find(photo_id).selected = selected
            self._save()

    def delete(self, photo_id: str) -> None:
        with self._lock:
            self._index.photos.remove(self._find(photo_id))
            self._save()
        for path in (self.image_path(photo_id), self.thumb_path(photo_id)):
            path.unlink(missing_ok=True)

    def update_settings(self, changes: dict) -> PhotoSettings:
        with self._lock:
            data = asdict(self._index.settings)
            for key, value in changes.items():
                if key not in data:
                    raise ValueError(f"Unknown photo setting {key!r}")
                data[key] = int(value) if isinstance(data[key], int) else value
            new = PhotoSettings(**data)
            new.validate()
            self._index.settings = new
            self._save()
            return PhotoSettings(**asdict(new))

    # ----------------------------------------------------------------- internal

    def _find(self, photo_id: str) -> Photo:
        for p in self._index.photos:
            if p.id == photo_id:
                return p
        raise KeyError(photo_id)

    def _load(self) -> _Index:
        try:
            raw = json.loads(self._index_file.read_text())
            photos = [Photo(**p) for p in raw.get("photos", [])]
            settings = PhotoSettings(**raw.get("settings", {}))
            settings.validate()
        except FileNotFoundError:
            return _Index()
        except (OSError, ValueError, TypeError) as e:
            log.warning("Ignoring invalid %s: %s", self._index_file, e)
            return _Index()
        # Drop entries whose image went missing (e.g. data/ copied by hand).
        return _Index([p for p in photos if self.image_path(p.id).exists()], settings)

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self._index), indent=2))
        tmp.replace(self._index_file)


def _safe(photo_id: str) -> str:
    if not photo_id.isalnum():
        raise KeyError(photo_id)
    return photo_id
