"""Index artwork providers and export a standalone edited copy of a mod."""

from collections import defaultdict
from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from cache import TextureCache, asset_key, card_art


OFFICIAL_DLC = {"bob", "ep1", *(f"dlc{i}" for i in range(1, 17))}


@dataclass
class Artwork:
    file: Path
    texture: object = None


class NoArtwork(ValueError):
    pass


class GameArtwork:
    """Read-only artwork from shipped game content and official DLC."""

    def __init__(self, folder):
        self.root = Path(folder).resolve(strict=True)
        content = self.root / "content"
        if not content.is_dir() or not (self.root / "bin").is_dir():
            raise ValueError("Choose the main The Witcher 3 folder containing bin and content.")
        roots = [content]
        dlc = self.root / "DLC"
        if dlc.is_dir():
            roots.extend(path for path in sorted(dlc.iterdir()) if path.is_dir() and path.name.casefold() in OFFICIAL_DLC)
        self.artwork = {}
        self.bundled = set()
        self.warnings = []
        for official_root in roots:
            if official_root.is_symlink() or official_root.is_junction():
                continue
            for directory, folders, names in official_root.walk(on_error=self.warn):
                folders[:] = [name for name in sorted(folders) if not (directory / name).is_junction()]
                for name in sorted(names):
                    path = directory / name
                    if path.is_symlink():
                        continue
                    if name.casefold() == "texture.cache" or card_art(path.as_posix()):
                        try:
                            self.index(path, official_root)
                        except (OSError, ValueError) as error:
                            self.warn(error)
        if not self.artwork:
            detail = f"\n{self.warnings[0]}" if self.warnings else ""
            raise NoArtwork(f"No original Gwent artwork found in the game files.{detail}")

    def warn(self, error):
        self.warnings.append(str(error))

    def index(self, path, official_root):
        if path.name.casefold() == "texture.cache":
            cache = TextureCache(path)
            for texture in cache.textures:
                if card_art(texture.path):
                    self.artwork.setdefault(texture.path, (cache, texture))
            return
        parts = path.relative_to(official_root).parts
        start = next((i for i, part in enumerate(parts) if part.casefold() == "gameplay"), None)
        if start is not None:
            key = Path(*parts[start:]).as_posix()
            if official_root != self.root / "content":
                key = f"dlc/{official_root.name}/data/{key}"
            self.artwork.setdefault(asset_key(key), (path, None))

    def preview(self, key):
        from PIL import Image

        source, texture = self.artwork[key]
        if texture:
            return source.preview(texture)
        with Image.open(source) as image:
            return image.convert("RGBA")


def installed_artwork_mods(game):
    candidates = {}
    warnings = []
    for name in ("Mods", "DLC"):
        directory = game.root / name
        if not directory.is_dir():
            continue
        sources = vortex_sources(directory, warnings)
        for path in sorted(directory.iterdir()):
            if name == "DLC" and path.name.casefold() in OFFICIAL_DLC:
                continue
            if not (path / "content").is_dir():
                continue
            try:
                mod = Mod(path)
                label = f"{name}/{path.name}"
                entries = {art.file for arts in mod.artwork.values() for art in arts}
                packages = {
                    sources[path / entry] for entry in entries if path / entry in sources
                }
                if packages and all(path / entry in sources for entry in entries):
                    for package in sorted(packages):
                        source = package / path.name if (package / path.name / "content").is_dir() else package
                        try:
                            staged = Mod(source)
                            staged.load_name, staged.load_folder = path.name, name
                            candidates[f"{label} — {package.name}"] = staged
                        except (OSError, ValueError) as error:
                            warnings.append(f"{package.name}: {error}")
                else:
                    mod.load_name, mod.load_folder = path.name, name
                    candidates[label] = mod
            except NoArtwork:
                continue
            except (OSError, ValueError) as error:
                warnings.append(f"{name}/{path.name}: {error}")
    return candidates, warnings


def is_game_folder(path):
    try:
        path = Path(path)
        return (path / "content").is_dir() and (path / "bin").is_dir() and any(
            (path / "bin" / name / "witcher3.exe").is_file() for name in ("x64", "x64_dx12")
        )
    except OSError:
        return False


def settings_file():
    import os

    return Path(os.environ.get("APPDATA") or Path.home()) / "GwentArtworkComposer" / "settings.json"


def remember_game_folder(folder):
    try:
        path = settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"game": str(folder)}), encoding="utf-8")
    except OSError:
        pass


def _registry_values(root, key, names):
    try:
        import winreg

        with winreg.OpenKey(root, key) as handle:
            return [winreg.QueryValueEx(handle, name)[0] for name in names]
    except (ImportError, OSError):
        return None


def _registry_subkeys(root, key):
    try:
        import winreg

        with winreg.OpenKey(root, key) as handle:
            return [f"{key}\\{winreg.EnumKey(handle, i)}" for i in range(winreg.QueryInfoKey(handle)[0])]
    except (ImportError, OSError):
        return []


def game_folder_candidates():
    """Likely The Witcher 3 install folders: last used, Steam libraries, GOG, Epic, common paths."""
    import re
    import string

    candidates = []
    try:
        candidates.append(json.loads(settings_file().read_text(encoding="utf-8"))["game"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        import winreg

        hives = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    except ImportError:
        hives = ()
    steam_roots = []
    for hive, key, name in [(hive, key, name) for hive in hives for key, name in (
        (r"Software\Valve\Steam", "SteamPath"), (r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (r"SOFTWARE\Valve\Steam", "InstallPath"),
    )]:
        value = _registry_values(hive, key, [name])
        if value:
            steam_roots.append(Path(value[0]))
    for steam in steam_roots:
        libraries = [steam]
        try:
            text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
            libraries += [Path(match.replace("\\\\", "\\")) for match in re.findall(r'"path"\s+"([^"]+)"', text)]
        except OSError:
            pass
        candidates += [library / "steamapps" / "common" / "The Witcher 3" for library in libraries]
    for hive in hives:
        for root_key in (r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games"):
            for key in _registry_subkeys(hive, root_key):
                value = _registry_values(hive, key, ["path"])
                if value:
                    candidates.append(value[0])
    for drive in (f"{letter}:\\" for letter in string.ascii_uppercase):
        if not Path(drive).exists():
            continue
        for relative in (
            r"SteamLibrary\steamapps\common\The Witcher 3",
            r"Steam\steamapps\common\The Witcher 3",
            r"Program Files (x86)\Steam\steamapps\common\The Witcher 3",
            r"Program Files\Steam\steamapps\common\The Witcher 3",
            r"GOG Games\The Witcher 3 Wild Hunt GOTY",
            r"GOG Games\The Witcher 3 Wild Hunt",
            r"Program Files (x86)\GOG Galaxy\Games\The Witcher 3 Wild Hunt GOTY",
            r"Program Files\Epic Games\TheWitcher3",
            r"Epic Games\TheWitcher3",
        ):
            candidates.append(Path(drive) / relative)
    seen = set()
    for candidate in candidates:
        key = str(candidate).casefold().rstrip("\\/")
        if key not in seen:
            seen.add(key)
            yield Path(candidate)


def find_game_folder():
    return next((path for path in game_folder_candidates() if is_game_folder(path)), None)


def documents_folder():
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Documents"


def parse_load_order(text):
    """Map mod folder name (casefolded) to (enabled, priority) from mods.settings text."""
    order = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().casefold()
            order.setdefault(section, [True, None])
        elif section and "=" in line:
            name, value = (part.strip() for part in line.split("=", 1))
            try:
                if name.casefold() == "enabled":
                    order[section][0] = int(value) != 0
                elif name.casefold() == "priority":
                    order[section][1] = int(value)
            except ValueError:
                pass
    return {name: (enabled, priority) for name, (enabled, priority) in order.items() if priority is not None or not enabled}


def read_load_order(path=None):
    path = Path(path) if path else documents_folder() / "The Witcher 3" / "mods.settings"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}, [f"Load order file not found ({path}). Defaults use mod folder names in alphabetical order."]
    except OSError as error:
        return {}, [f"Could not read load order {path}: {error}"]
    for encoding in ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "latin-1"):
        try:
            return parse_load_order(data.decode(encoding)), []
        except UnicodeDecodeError:
            continue
    return {}, [f"Could not decode load order {path}."]


def vortex_sources(directory, warnings):
    sources = {}
    for manifest in directory.glob("vortex.deployment*.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or data.get("gameId") != "witcher3":
                continue
            if Path(data["targetPath"]).resolve() != directory.resolve():
                continue
            staging = Path(data["stagingPath"]).resolve(strict=True)
            for record in data["files"]:
                source = record.get("source")
                relative = record.get("relPath")
                if not source or not relative or Path(source).name != source or source in (".", ".."):
                    continue
                deployed = (directory / relative)
                if not deployed.is_relative_to(directory) or ".." in deployed.parts:
                    continue
                package = (staging / source).resolve()
                if package.is_relative_to(staging) and package.is_dir():
                    sources[deployed] = package
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
            warnings.append(f"{manifest.name}: {error}")
    return sources


def bundle_paths(path):
    with path.open("rb") as source:
        header = source.read(32)
        if len(header) != 32 or header[:8] != b"POTATO70":
            raise ValueError(f"Unknown bundle format: {path.name}")
        length, version = struct.unpack_from("<IH", header, 16)
        stride = {3: 320, 5: 304}.get(version)
        if not stride or length % stride or length > path.stat().st_size - 32:
            raise ValueError(f"Unsupported bundle index: {path.name}")
        for _ in range(length // stride):
            record = source.read(stride)
            yield asset_key(record[:256].split(b"\0", 1)[0].decode("latin-1"))


class Mod:
    def __init__(self, folder):
        root = Path(folder).resolve(strict=True)
        if root.name.casefold() == "content":
            root = root.parent
        self.root = root
        self.artwork = defaultdict(list)
        self.bundled = set()
        self.caches = {}
        self.files = {}
        self.stamps = {}
        direct_content = (root / "content").is_dir()
        self.prefix = Path(root.name) if direct_content else Path()
        if not direct_content and not any((root / name).is_dir() for name in ("mods", "dlc")):
            raise ValueError("Select a mod folder containing content, or a package containing Mods / DLC.")
        def fail(error):
            raise error

        for directory, folders, names in root.walk(on_error=fail):
            folders[:] = [name for name in folders if name not in (".gwent-forge-backups", "__pycache__")]
            for name in folders:
                path = directory / name
                if path.is_symlink() or path.is_junction():
                    raise ValueError(f"Linked directory: {path}. Select the actual Vortex staging folder.")
            for name in names:
                if name.startswith(".gwent-forge-") or name == "__folder_managed_by_vortex":
                    continue
                path = directory / name
                relative = path.relative_to(root)
                resolved = path.resolve(strict=True)
                if not resolved.is_file():
                    raise ValueError(f"Mod entry is not a file: {relative}")
                self.files[relative] = resolved
                self.stamps[relative] = self.stamp(resolved)
                content_index = next((i for i, part in enumerate(relative.parts) if part.casefold() == "content"), None)
                if content_index is None:
                    continue
                content_path = Path(*relative.parts[content_index + 1:]).as_posix()
                if name.casefold() == "texture.cache" and self.stamps[relative][2]:
                    cache = TextureCache(resolved)
                    self.caches[relative] = cache
                    for texture in cache.textures:
                        if card_art(texture.path):
                            self.artwork[texture.path].append(Artwork(relative, texture))
                elif path.suffix.casefold() == ".bundle":
                    if self.stamps[relative][2]:
                        self.bundled.update(key for key in bundle_paths(resolved) if card_art(key))
                elif card_art(content_path):
                    self.artwork[asset_key(content_path)].append(Artwork(relative))
        if not self.artwork and not self.bundled:
            raise NoArtwork("No Gwent artwork found in this mod package.")

    @staticmethod
    def stamp(path):
        info = path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    def check_source(self):
        for relative, source in self.files.items():
            link = self.root / relative
            if link.resolve(strict=True) != source or self.stamp(source) != self.stamps[relative]:
                raise ValueError("Mod files changed after opening. Open the mod again and retry.")

    def preview(self, key):
        from PIL import Image

        for art in self.artwork.get(key, []):
            if art.texture is None:
                with Image.open(self.files[art.file]) as image:
                    return image.convert("RGBA")
        for art in self.artwork.get(key, []):
            return self.caches[art.file].preview(art.texture)
        raise ValueError("Artwork is not available for preview.")

    def export(self, destination, removed):
        destination = Path(destination).resolve()
        if destination.is_relative_to(self.root) or destination in self.files.values():
            raise ValueError("Save the ZIP to a location outside the mod folder.")
        if not removed or not removed <= self.artwork.keys() or removed & self.bundled:
            raise ValueError("Select at least one removable artwork entry.")
        self.check_source()
        edits = defaultdict(set)
        skip = set()
        for key in removed:
            for art in self.artwork[key]:
                if art.texture is None:
                    skip.add(art.file)
                else:
                    edits[art.file].add(key)
        with tempfile.TemporaryDirectory(prefix=".gwent-art-", dir=destination.parent) as temp:
            archive_path = Path(temp) / "export.zip"
            cache_path = Path(temp) / "texture.cache"
            with ZipFile(archive_path, "w", ZIP_DEFLATED, compresslevel=3) as archive:
                for relative, source in self.files.items():
                    if relative in skip:
                        continue
                    if relative in edits:
                        cache = self.caches[relative]
                        if all(texture.path in edits[relative] for texture in cache.textures):
                            continue
                        cache.write_without(cache_path, edits[relative])
                        source = cache_path
                    archive.write(source, (self.prefix / relative).as_posix())
                if not archive.namelist():
                    raise ValueError("Removing the selected artwork leaves an empty mod. Disable that mod in Vortex.")
            self.check_source()
            os.replace(archive_path, destination)
