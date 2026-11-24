"""Per-artwork source selection and export of an artwork-only Witcher 3 mod."""

from collections import defaultdict
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import tempfile
import zlib
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from bundle import write_bundle, write_metadata
from cache import TextureCache
from mod import GameArtwork, installed_artwork_mods, read_load_order
from names import card_xml_catalog, faithful_xml_overrides, selected_card_data


ORIGINAL = "Game original"
NONE = "No texture"
UPLOAD = "Uploaded image"
MOD_NAME = "mod0000GwentArtworkComposer"
PROJECT_FORMAT = "GwentArtworkComposer project"
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".dds")


def name_roots(game_root, load_order):
    """Folders holding card definitions and strings, ordered so the winning mod comes last."""
    root = Path(game_root)
    # The composer's own export only holds a generated copy of Faithful's XML; read the original instead.
    mods = [
        path for path in (root / "Mods").iterdir() if path.is_dir() and path.name.casefold() != MOD_NAME.casefold()
    ] if (root / "Mods").is_dir() else []

    def rank(path):
        setting = load_order.get(path.name.casefold())
        if setting is None:
            return 1, 0, path.name.casefold()
        enabled, priority = setting
        return (0, priority, path.name.casefold()) if enabled else None

    ranked = sorted((path for path in mods if rank(path)), key=rank, reverse=True)
    return [root / "content", root / "DLC", *ranked]


def load_rank(provider, load_order):
    """Sort key for the game's texture priority (lower wins), or None if not loaded/unknown."""
    name = getattr(provider, "load_name", None)
    if not name:
        return None
    if getattr(provider, "load_folder", "Mods") == "DLC":
        return 2, 0, name.casefold()
    setting = load_order.get(name.casefold())
    if setting is None:
        return 1, 0, name.casefold()
    enabled, priority = setting
    return (0, priority, name.casefold()) if enabled else None


def alternate_base(key):
    path = PurePosixPath(key)
    stem = re.sub(r"_alt\d*$", "", path.stem)
    return str(path.with_name(stem + path.suffix))


def empty_metadata(version):
    if version not in (6, 7):
        raise ValueError("Unsupported metadata version.")
    # Null file/entry/bundle records and an empty root directory; no gameplay assets.
    return (
        b"\x03VTM" + struct.pack("<3I", version, 0, 0) + b"\x02\0\0"
        + b"\x01" + bytes(32)
        + b"\x01" + bytes(24 if version == 7 else 20)
        + b"\x01" + bytes(24)
        + b"\x80\x01" + struct.pack("<2I", 1, 0)
        + b"\x80\x80"
    )


def cached_texture(provider, key):
    if isinstance(provider, GameArtwork):
        entry = provider.artwork.get(key)
        return entry if entry and isinstance(entry[0], TextureCache) else None
    for art in provider.artwork.get(key, []):
        if art.texture is not None:
            return provider.caches[art.file], art.texture
    return None


class ArtworkLibrary:
    def __init__(self, game, mods, load_order=None, links=None, used=None, xml_catalog=None,
                 main_xml_id=None, manual_links=None):
        self.game = game
        self.load_order = load_order or {}
        self.xml_catalog = xml_catalog
        self.main_xml_id = main_xml_id or (xml_catalog or {}).get("default_main")
        self.manual_links = dict(manual_links or {})
        if xml_catalog:
            names, used, links = selected_card_data(xml_catalog, self.main_xml_id)
        else:
            names = {}
        self.picture_links = dict(links or {})
        self.used_pictures = used
        self.ranks = {label: load_rank(mod, self.load_order) for label, mod in mods.items()}
        ranked = sorted((label for label in mods if self.ranks[label]), key=self.ranks.get)
        unranked = [label for label in mods if not self.ranks[label]]
        self.providers = {ORIGINAL: game, **{label: mods[label] for label in ranked + unranked}}
        found = {key for provider in self.providers.values() for key in set(provider.artwork) | provider.bundled}
        if used:
            found.update(f"gameplay/gui_new/icons/gwint/{picture}.png" for picture in used)
        direct = {
            key: tuple(label for label, provider in self.providers.items() if cached_texture(provider, key))
            for key in found
        }
        # Path the game loads for a card -> path the base game used for it (where other mods put art).
        self.links = {}
        by_stem = defaultdict(list)
        for key in sorted(found, key=lambda key: (not key.startswith("gameplay/"), key)):
            by_stem[PurePosixPath(key).stem].append(key)
        for new, old in self.picture_links.items():
            sources = [key for key in by_stem.get(old, []) if direct[key]]
            if not sources:
                continue
            for key in by_stem.get(new) or [f"gameplay/gui_new/icons/gwint/{new}.png"]:
                self.links[key] = sources[0]
                direct.setdefault(key, ())
        self.direct = direct
        self.paths = sorted(direct, key=lambda key: (PurePosixPath(key).name, key))
        # Same card per the XMLs but stored at another path in a source (e.g. faithful_geralt for neu_geralt).
        self.xml_matches = self._find_xml_matches(direct)
        self.available = {}
        for key in self.paths:
            old = self.links.get(key)
            manual = self.manual_links.get(key)
            self.available[key] = tuple(
                label for label in self.providers
                if label in direct[key] or (manual and label in direct.get(manual, ()))
                or label in self.xml_matches.get(key, {}) or (old and label in direct[old])
            )
        self.groups = {}
        for key in self.paths:
            self.groups.setdefault(alternate_base(key), []).append(key)
        for base, paths in self.groups.items():
            paths.sort(key=lambda key: (
                key != base,
                int(re.search(r"_alt(\d*)$", PurePosixPath(key).stem)[1] or 0) if key != base else 0,
            ))
        # Source the game would use per mods.settings load order (shown for reference only).
        self.load_order_defaults = {}
        for key, sources in self.direct.items():
            self.load_order_defaults[key] = next(
                (label for label in ranked if label in sources),
                ORIGINAL if ORIGINAL in sources else NONE,
            )
        # Every path starts as No texture: only cards the user picks are exported.
        self.defaults = dict.fromkeys(self.available, NONE)
        # Only choices that differ from the default (No texture).
        self.choices = {}
        # Paths the user has picked a source for (even if it equals the default).
        self.edited = set()
        # Center-cropped RGBA images uploaded per path, sized to that path's texture.
        self.uploads = {}
        # Localized card titles by lowercase picture name.
        self.names = names

    @classmethod
    def scan(cls, folder):
        game = GameArtwork(folder)
        mods, warnings = installed_artwork_mods(game)
        load_order, order_warnings = read_load_order()
        catalog = None
        try:
            catalog = card_xml_catalog(name_roots(game.root, load_order))
        except OSError as error:
            warnings.append(f"Card names unavailable: {error}")
        library = cls(game, mods, load_order, xml_catalog=catalog)
        if catalog and catalog["patches"]:
            _, (total, mapped, omitted) = faithful_xml_overrides(catalog, library.main_xml_id)
            if catalog["options"]:
                warnings.append(
                    f"Faithful artwork XML found: export will include a compatibility override "
                    f"({mapped}/{total} card patches matched; {omitted} omitted)."
                )
            else:
                warnings.append("Faithful artwork XML found, but no main card XML was available to build an override.")
        return library, game.warnings + warnings + order_warnings

    def rebuilt(self, mods):
        """New library over these mod sources, keeping load order and card definition data."""
        return self.with_configuration(mods=mods)

    def with_configuration(self, main_xml_id=None, manual_links=None, mods=None):
        """Return a library using these XML and cross-card matches, preserving compatible choices."""
        if mods is None:
            mods = {label: provider for label, provider in self.providers.items() if label != ORIGINAL}
        library = ArtworkLibrary(
            self.game, mods, self.load_order,
            links=None if self.xml_catalog else self.picture_links,
            used=None if self.xml_catalog else self.used_pictures,
            xml_catalog=self.xml_catalog,
            main_xml_id=main_xml_id or self.main_xml_id,
            manual_links=self.manual_links if manual_links is None else manual_links,
        )
        library.uploads = {key: image for key, image in self.uploads.items() if key in library.available}
        for key, label in self.choices.items():
            if key in library.available and (
                label in (NONE, UPLOAD) or label in library.available[key]
            ) and (label != UPLOAD or key in library.uploads):
                library.choose([key], label)
        library.edited = self.edited & library.available.keys()
        return library

    def with_card_match(self, target, donor):
        if target not in self.available or donor not in self.available or target == donor:
            raise ValueError("Choose two different artwork paths from this scan.")
        if not any(self.direct.get(donor, ())):
            raise ValueError("The matched card has no usable texture source.")
        matches = dict(self.manual_links)
        matches[target] = donor
        return self.with_configuration(manual_links=matches)

    def without_card_match(self, target):
        if target not in self.manual_links:
            return self
        matches = dict(self.manual_links)
        del matches[target]
        return self.with_configuration(manual_links=matches)

    def in_use(self, key):
        """False when no winning card definition points to this path (the game never shows it)."""
        if self.used_pictures is None:
            return True
        stem = re.sub(r"_dlc$", "", PurePosixPath(alternate_base(key)).stem)
        return stem in self.used_pictures or stem == "cardcover"

    def linked_from(self, key, label):
        """Other artwork path a source's texture is borrowed from, or None if it is direct."""
        if label in self.direct.get(key, ()):
            return None
        manual = self.manual_links.get(key)
        if manual and label in self.direct.get(manual, ()):
            return manual
        if label in self.xml_matches.get(key, {}):
            return self.xml_matches[key][label]
        old = self.links.get(key)
        return old if old and label in self.direct.get(old, ()) else None

    def entry(self, key, label):
        """(cache, texture record stored under key) for a source, following card links."""
        provider = self.providers[label]
        found = cached_texture(provider, key)
        if found:
            return found
        donor = self.linked_from(key, label)
        found = cached_texture(provider, donor) if donor else None
        return (found[0], found[1].renamed(key)) if found and donor != key else None

    def card_name(self, key):
        """Localized card title for a path, or None when the game files do not define one."""
        base = PurePosixPath(alternate_base(key)).stem
        name = self.names.get(base) or self.names.get(re.sub(r"_dlc$", "", base))
        if name and key != alternate_base(key):
            number = re.search(r"_alt(\d*)$", PurePosixPath(key).stem)[1] or "1"
            name = f"{name} (alt {number})"
        return name

    def source_paths(self, label):
        """Artwork paths a source ships its own texture for."""
        return [key for key in self.paths if label in self.direct.get(key, ())]

    def _xml_records(self, label):
        """(card records from XMLs the source ships plus artwork patch XMLs, base game card records)."""
        catalog = self.xml_catalog
        if not catalog:
            return [], []
        own = []
        if label != ORIGINAL and label in self.providers:
            source = f"Mods/{self.providers[label].root.name}".casefold()
            own = [
                record for item in catalog["definitions"] + catalog["patches"]
                if item["source"].casefold() == source for record in item["records"]
            ]
        # Patch XMLs (Faithful) often live in a DLC folder apart from the mod holding their textures.
        own += [record for patch in catalog["patches"] for record in patch["records"]]
        base = [record for item in catalog["definitions"] if not item["is_mod"] for record in item["records"]]
        return own, base

    def source_card_name(self, key, label):
        """Card title a source's artwork at this path was made for (its own XML, else the base game's)."""
        stem = PurePosixPath(alternate_base(key)).stem.casefold()
        own, base = self._xml_records(label)
        base_names = {
            record["name"].casefold(): record["title_text"] for record in base
            if record.get("name") and record.get("title_text")
        }
        for records in (own, base):
            for record in records:
                pictures = {(record.get(field) or "").casefold() for field in ("picture", "dlcPicture")}
                name = record.get("title_text") or base_names.get((record.get("name") or "").casefold())
                if (stem in pictures or re.sub(r"_dlc$", "", stem) in pictures) and name:
                    if key != alternate_base(key):
                        name += f" (alt {re.search(r'_alt(\d*)$', PurePosixPath(key).stem)[1] or '1'})"
                    return name
        return self.card_name(key)

    def xml_donor(self, key, label):
        """Path whose texture in this source is the same card per the card XMLs, or None."""
        direct = lambda path: path and label in self.direct.get(path, ())
        manual = self.manual_links.get(key)
        if direct(manual):
            return manual
        if direct(key):
            return key
        found = self.xml_matches.get(key, {}).get(label)
        if found:
            return found
        old = self.links.get(key)
        return old if direct(old) else None

    def _find_xml_matches(self, direct):
        """{path: {source: other path}} where a source's art for the same card (by XML title) sits elsewhere."""
        catalog = self.xml_catalog
        if not catalog:
            return {}
        active = dict(catalog["active"])
        selected = next((item for item in catalog["options"] if item["id"] == self.main_xml_id), None)
        if selected and catalog["main_asset"]:
            active[catalog["main_asset"]] = selected
        targets = defaultdict(list)
        for definition in active.values():
            for record in definition["records"]:
                if record.get("picture"):
                    targets[record["picture"].casefold()].append(record)
        patches = [record for patch in catalog["patches"] for record in patch["records"]]
        base = [record for item in catalog["definitions"] if not item["is_mod"] for record in item["records"]]
        base_titles = defaultdict(set)
        for record in base:
            if record.get("name"):
                base_titles[record["name"].casefold()].add((record.get("title") or "").casefold())

        def index(records):
            found = defaultdict(list)
            for record in records:
                pictures = [record[field].casefold() for field in ("picture", "dlcPicture") if record.get(field)]
                if record.get("title"):
                    found["t:" + record["title"].casefold()] += pictures
                if record.get("title_text"):
                    found["x:" + record["title_text"].casefold()] += pictures
                if not record.get("title") and record.get("name"):
                    found["n:" + record["name"].casefold()] += pictures
            return found

        base_index = index(base)
        by_stem = defaultdict(list)
        for path in self.paths:
            by_stem[PurePosixPath(path).stem.casefold()].append(path)
        matches = {}
        for label, provider in self.providers.items():
            own = list(patches)
            if label != ORIGINAL:
                source = f"Mods/{provider.root.name}".casefold()
                own = [
                    record for item in catalog["definitions"] + catalog["patches"]
                    if item["source"].casefold() == source for record in item["records"]
                ] + own
            indexes = (index(own), base_index)
            for key in self.paths:
                if label in direct[key]:
                    continue
                stem = PurePosixPath(alternate_base(key)).stem.casefold()
                suffix = PurePosixPath(key).stem.casefold()[len(stem):]
                records = targets.get(stem) or targets.get(re.sub(r"_dlc$", "", stem)) or ()
                wanted = []
                for record in records:
                    if record.get("title"):
                        wanted.append("t:" + record["title"].casefold())
                    if record.get("title_text"):
                        wanted.append("x:" + record["title_text"].casefold())
                    name = (record.get("name") or "").casefold()
                    # Internal names identify a card only where the selected XML keeps the base game's title.
                    if name and (record.get("title") or "").casefold() in base_titles.get(name, {""}):
                        wanted.append("n:" + name)
                donor = None
                for found in indexes:
                    for picture in (picture for item in wanted for picture in found.get(item, ())):
                        for name in (picture + suffix, picture) if suffix else (picture,):
                            donor = next((path for path in by_stem.get(name, ()) if label in direct[path]), None)
                            if donor:
                                break
                        if donor:
                            break
                    if donor:
                        break
                if donor and donor != key:
                    matches.setdefault(key, {})[label] = donor
        return matches

    def chosen_source(self, key):
        return self.choices.get(key, self.defaults[key])

    def export_keys(self):
        return [key for key in self.paths if self.chosen_source(key) != NONE]

    def choose(self, keys, label):
        keys = set(keys)
        if (label not in (NONE, UPLOAD) and label not in self.providers) or not keys <= self.available.keys():
            raise ValueError("Unknown artwork source or path.")
        if label == UPLOAD and not keys <= self.uploads.keys():
            raise ValueError("Upload an image for every selected card first.")
        if label not in (NONE, UPLOAD) and any(label not in self.available[key] for key in keys):
            raise ValueError("That source does not contain a cached texture for every selected card.")
        self.edited |= keys
        for key in keys:
            if label == self.defaults[key]:
                self.choices.pop(key, None)
            else:
                self.choices[key] = label

    def reset(self, keys):
        """Return paths to their default (No texture) and forget their uploads and Edited mark."""
        for key in keys:
            self.choices.pop(key, None)
            self.uploads.pop(key, None)
            self.edited.discard(key)

    def without_source(self, label):
        """Return (new library without this mod source, paths whose choice fell back to the default)."""
        if label not in self.providers or label == ORIGINAL:
            raise ValueError("Only mod sources can be removed.")
        mods = {name: provider for name, provider in self.providers.items() if name not in (ORIGINAL, label)}
        library = self.rebuilt(mods)
        library.uploads = {key: image for key, image in self.uploads.items() if key in library.available}
        reset = sorted(key for key in self.edited | self.choices.keys() if self.chosen_source(key) == label)
        for key, chosen in self.choices.items():
            if chosen != label and key in library.available:
                library.choose([key], chosen)
        library.edited = (self.edited & library.available.keys()) - set(reset)
        return library, reset

    def template(self, key):
        """Reference texture for a path's size and record fields: original first, then other sources."""
        labels = sorted(self.available[key], key=lambda label: label != ORIGINAL)
        if not labels:
            raise ValueError(f"No existing texture to match the size of {PurePosixPath(key).name}.")
        return self.entry(key, labels[0])[1]

    def fitted(self, image, keys):
        """Center-crop one RGBA image to each path's texture size."""
        from PIL import Image, ImageOps

        sizes = {key: tuple(self.template(key).fields[6:8]) for key in keys}
        return {
            key: image if image.size == size else ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            for key, size in sizes.items()
        }

    @staticmethod
    def open_image(path):
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            return ImageOps.exif_transpose(source).convert("RGBA")

    def upload(self, keys, image_path):
        keys = list(keys)
        if not set(keys) <= self.available.keys():
            raise ValueError("Unknown artwork path.")
        self.apply_uploads(self.fitted(self.open_image(image_path), keys))

    def apply_uploads(self, images):
        self.uploads.update(images)
        self.choose(images, UPLOAD)

    def match_images(self, items):
        """Map dropped files/folders to paths: exact asset path first, then by file name."""
        usable = [key for key in self.paths if self.available[key]]
        by_path = {str(PurePosixPath(key).with_suffix("")): key for key in usable}
        by_stem = defaultdict(list)
        for key in usable:
            by_stem[PurePosixPath(key).stem].append(key)
        files = []
        for item in map(Path, items):
            if item.is_dir():
                files.extend(
                    (path, path.relative_to(item).with_suffix("").as_posix().casefold())
                    for path in sorted(item.rglob("*"))
                    if path.is_file() and path.suffix.casefold() in IMAGE_TYPES
                )
            elif item.suffix.casefold() in IMAGE_TYPES:
                files.append((item, item.stem.casefold()))
        exact, by_name, unmatched = {}, [], []
        for path, relative in files:
            candidates = [key for stem, key in by_path.items() if stem == relative or stem.endswith("/" + relative)]
            if "/" in relative and len(candidates) == 1:
                exact[candidates[0]] = path
            elif PurePosixPath(relative).name in by_stem:
                by_name.append((path, by_stem[PurePosixPath(relative).name]))
            else:
                unmatched.append(str(path))
        matches = defaultdict(list)
        for key, path in exact.items():
            matches[path].append(key)
        for path, keys in by_name:
            keys = [key for key in keys if key not in exact]
            if keys:
                matches[path].extend(keys)
        return dict(matches), unmatched

    def prepare_import(self, items):
        """Load and crop matching images without changing choices (safe for a worker thread)."""
        from PIL import Image

        matches, unmatched = self.match_images(items)
        images, errors = {}, []
        for path, keys in matches.items():
            try:
                images.update(self.fitted(self.open_image(path), keys))
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                errors.append(f"{path.name}: {error}")
        return images, unmatched, errors

    def source_reference(self, label):
        if label in (ORIGINAL, NONE, UPLOAD):
            return {"source": label}
        return {"source": label, "folder": self.providers[label].root.name}

    def save_project(self, destination):
        destination = Path(destination).resolve()
        uploads = {key: f"uploads/{index:04d}.png" for index, key in enumerate(sorted(self.uploads))}
        project = {
            "format": PROJECT_FORMAT,
            "version": 2,
            "main_xml": self.main_xml_id,
            "manual_links": self.manual_links,
            # Edited paths that kept their default are saved too, so the Edited mark survives reopening.
            "choices": {
                key: self.source_reference(self.chosen_source(key))
                for key in sorted(self.choices.keys() | self.edited)
            },
            "uploads": uploads,
        }
        handle, temporary = tempfile.mkstemp(prefix=".gwent-project-", suffix=".tmp", dir=destination.parent)
        os.close(handle)
        try:
            with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=3) as archive:
                archive.writestr("project.json", json.dumps(project, indent=1, ensure_ascii=False))
                for key, name in uploads.items():
                    buffer = io.BytesIO()
                    self.uploads[key].save(buffer, "PNG")
                    archive.writestr(name, buffer.getvalue())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def read_project(self, path):
        """Parse project choices, uploads, XML choice and card matches without changing this library."""
        from PIL import Image

        warnings = []
        try:
            with ZipFile(path) as archive:
                project = json.loads(archive.read("project.json").decode("utf-8"))
                if not isinstance(project, dict) or project.get("format") != PROJECT_FORMAT:
                    raise ValueError("This is not a Gwent Artwork Composer project.")
                if project.get("version") not in (1, 2):
                    raise ValueError("This project was saved by a newer version of the app.")
        except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            raise ValueError(f"Could not read project: {error}") from error

        main_xml_id = project.get("main_xml", self.main_xml_id)
        available_xml = {option["id"] for option in (self.xml_catalog or {}).get("options", ())}
        if main_xml_id is not None and (
            not isinstance(main_xml_id, str) or main_xml_id not in available_xml
        ):
            if main_xml_id is not None:
                warnings.append(f"Card XML choice is not available in this scan: {main_xml_id}")
            main_xml_id = self.main_xml_id
        requested_links = project.get("manual_links", {}) if project.get("version") == 2 else {}
        if not isinstance(requested_links, dict):
            requested_links = {}
            warnings.append("Card matches were invalid and skipped.")
        base_configuration = self.with_configuration(main_xml_id, {})
        manual_links = {}
        for target, donor in requested_links.items():
            if (
                isinstance(target, str) and isinstance(donor, str)
                and target in base_configuration.available and donor in base_configuration.available
                and target != donor and base_configuration.direct.get(donor)
            ):
                manual_links[target] = donor
            else:
                warnings.append(f"Card match skipped, path is not available: {target} -> {donor}")
        configured = base_configuration.with_configuration(main_xml_id, manual_links)

        try:
            with ZipFile(path) as archive:
                project = json.loads(archive.read("project.json").decode("utf-8"))
                uploads = {}
                for key, name in dict(project.get("uploads", {})).items():
                    if key not in configured.available:
                        warnings.append(f"Upload skipped, path not in this game/mod scan: {key}")
                        continue
                    try:
                        with Image.open(io.BytesIO(archive.read(name))) as image:
                            uploads.update(configured.fitted(image.convert("RGBA"), [key]))
                    except (KeyError, OSError, ValueError, Image.DecompressionBombError) as error:
                        warnings.append(f"Upload skipped for {key}: {error}")
        except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            raise ValueError(f"Could not read project: {error}") from error

        folders = {}
        for label, provider in configured.providers.items():
            folders.setdefault(provider.root.name.casefold(), label)
        choices = {}
        for key, reference in dict(project.get("choices", {})).items():
            label = reference.get("source") if isinstance(reference, dict) else None
            ref_folder = reference.get("folder") if isinstance(reference, dict) else None
            ref_source = reference.get("source") if isinstance(reference, dict) else None
            if key not in configured.available:
                warnings.append(f"Skipped, path not in this game/mod scan: {key}")
                continue
            if label not in (ORIGINAL, NONE, UPLOAD) and label not in configured.providers:
                label = folders.get(str(ref_folder or "").casefold())
            if label is None:
                warnings.append(f"Skipped, source mod not installed: {key} ({ref_folder or ref_source})")
            elif label == UPLOAD and key not in uploads:
                warnings.append(f"Skipped, uploaded image missing: {key}")
            elif label not in (NONE, UPLOAD) and label not in configured.available[key]:
                warnings.append(f"Skipped, {ref_folder or label} no longer has: {key}")
            else:
                choices[key] = label
        return choices, uploads, warnings, main_xml_id, manual_links

    def apply_project(self, choices, uploads):
        self.choices = {}
        self.edited = set()
        self.uploads = dict(uploads)
        for key, label in choices.items():
            self.choose([key], label)

    def preview(self, key, label):
        if label == UPLOAD:
            if key not in self.uploads:
                raise ValueError("No image uploaded for this card.")
            return self.uploads[key]
        entry = self.entry(key, label)
        if not entry:
            raise ValueError("No cached texture at this path in the selected source.")
        cache, texture = entry
        cache.check_source()
        return cache.preview(texture)

    def export_png(self, keys, folder=None, single=None):
        """Save each path's chosen texture as PNG; folder keeps asset paths, single writes one file."""
        targets = {}
        for key in keys:
            if self.chosen_source(key) == NONE:
                continue
            targets[key] = Path(single) if single else Path(folder) / Path(*PurePosixPath(key).with_suffix(".png").parts)
        if not targets:
            raise ValueError("Nothing to export: the selected cards are set to No texture.")
        for target in targets.values():
            resolved = target.resolve()
            if any(resolved.is_relative_to(provider.root.resolve()) for provider in self.providers.values()):
                raise ValueError("Save PNG files outside the game and source mod folders.")
        errors = []
        for key, target in targets.items():
            try:
                image = self.preview(key, self.chosen_source(key))
                target.parent.mkdir(parents=True, exist_ok=True)
                image.save(target, "PNG")
            except (OSError, ValueError, RuntimeError, struct.error, zlib.error) as error:
                errors.append(f"{key}: {error}")
        return len(targets) - len(errors), errors

    def export(self, destination):
        destination = Path(destination).resolve()
        if any(destination.is_relative_to(provider.root.resolve()) for provider in self.providers.values()):
            raise ValueError("Save the ZIP outside the game and source mod folders.")
        keys = self.export_keys()
        overrides, _ = faithful_xml_overrides(self.xml_catalog, self.main_xml_id)
        if not keys and not overrides:
            raise ValueError("Nothing to export: every card is set to No texture.")
        entries = []
        uploads = []
        for key in keys:
            label = self.chosen_source(key)
            if label == UPLOAD:
                if key not in self.uploads:
                    raise ValueError(f"Uploaded image missing: {key}")
                uploads.append((self.template(key), self.uploads[key]))
                entries.append(key)
                continue
            if label not in self.available[key]:
                raise ValueError(f"Invalid texture selection: {key}")
            entry = self.entry(key, label)
            if not entry:
                raise ValueError(f"Selected texture is no longer available: {key}. Scan again.")
            entries.append(entry)
        version = next(
            (source.version for source, texture in self.game.artwork.values()
             if texture is not None and isinstance(source, TextureCache)),
            next((entry[0].version for entry in entries if not isinstance(entry, str)), 7),
        )
        with tempfile.TemporaryDirectory(prefix="gwent-compose-", dir=destination.parent) as directory:
            temporary = Path(directory)
            cache_path = None
            if keys:
                cache_path = temporary / "texture.cache"
                if uploads:
                    uploaded_path = temporary / "uploads.cache"
                    TextureCache.write_images(uploaded_path, uploads, version)
                    uploaded = TextureCache(uploaded_path)
                    generated = {texture.path: (uploaded, texture) for texture in uploaded.textures}
                    entries = [generated[entry] if isinstance(entry, str) else entry for entry in entries]
                TextureCache.write_selected(cache_path, entries, version)
                repacked = TextureCache(cache_path)
                if {texture.path for texture in repacked.textures} != set(keys):
                    raise ValueError("Repacked texture paths do not match the selected cards.")
            for patch in (self.xml_catalog or {}).get("patches", ()):
                source = patch.get("container")
                if source:
                    try:
                        info = Path(source).stat()
                    except OSError as error:
                        raise ValueError(f"Faithful DLC bundle is no longer available: {source}") from error
                    stamp = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
                    if stamp != patch["container_stamp"]:
                        raise ValueError("Faithful DLC XML changed after scanning. Scan again before exporting.")
            archive_path = temporary / "artwork.zip"
            metadata = empty_metadata(version)
            bundle_data = None
            if overrides:
                bundle_version = 5 if version == 7 else 3
                metadata_version = 7 if bundle_version == 5 else 6
                bundle_data, bundle_records, data_start = write_bundle(overrides, bundle_version)
                metadata = write_metadata(
                    "blob0.bundle", overrides, bundle_records, data_start, metadata_version,
                )
            with ZipFile(archive_path, "w", ZIP_DEFLATED, compresslevel=3) as archive:
                if cache_path:
                    archive.write(cache_path, f"Mods/{MOD_NAME}/content/texture.cache")
                archive.writestr(f"Mods/{MOD_NAME}/content/metadata.store", metadata)
                if bundle_data:
                    archive.writestr(f"Mods/{MOD_NAME}/content/blob0.bundle", bundle_data)
            for cache, _ in entries:
                cache.check_source()
            os.replace(archive_path, destination)
