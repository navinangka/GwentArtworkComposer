"""Witcher 3 HCXT v6/v7 texture cache reader and selective repacker."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import struct
import zlib


PAGE = 4096
FOOTER = struct.Struct("<Q4I4sI")
ENTRY = struct.Struct("<Ii4I4Hiiq4B")
V7_FLAG = 0x10000


def asset_key(path):
    return path.replace("\\", "/").strip("/").casefold()


def card_art(path):
    parts = PurePosixPath(asset_key(path)).parts
    return (
        len(parts) >= 4
        and parts[-4:-1] == ("gui_new", "icons", "gwint")
        and parts[-1].endswith((".png", ".dds", ".xbm", ".tga"))
    )


FACTIONS = {
    "All factions": None,
    "Northern Realms": "nor",
    "Nilfgaard": "nil",
    "Monsters": "nml",
    "Scoia'tael": "sco",
    "Skellige": "ske",
    "Neutral": "neu",
    "Special": "spc",
    "Other": "other",
}


def card_faction(path):
    prefix = PurePosixPath(asset_key(path)).name.split("_", 1)[0]
    return prefix if prefix in FACTIONS.values() else "other"


def checksum(*tables):
    result = 14695981039346656037
    for table in tables:
        for byte in table:
            result = ((result ^ byte) * 1099511628211) & 0xffffffffffffffff
    return result


def name_hash(name):
    """Texture record hash of an asset name (Java-style h*31+c over the stored name)."""
    result = 0
    for character in name:
        result = (result * 31 + ord(character)) & 0xffffffff
    return result


@dataclass(frozen=True)
class Texture:
    name: bytes
    fields: tuple

    @property
    def path(self):
        return asset_key(self.name.decode("latin-1"))

    def renamed(self, path):
        """Same payload record stored under another asset path."""
        name = path.replace("/", "\\")
        return Texture(name.encode("latin-1"), (name_hash(name), *self.fields[1:]))

    @property
    def offset(self):
        return self.fields[2] * PAGE

    @property
    def size(self):
        return self.fields[3]


class TextureCache:
    def __init__(self, path):
        self.path = path.resolve(strict=True)
        self.stamp = self.file_stamp()
        with self.path.open("rb") as source:
            source.seek(0, 2)
            size = source.tell()
            if size < FOOTER.size:
                raise ValueError(f"Invalid texture cache: {path}")
            source.seek(-FOOTER.size, 2)
            crc, pages, count, string_size, mip_count, magic, version = FOOTER.unpack(source.read(FOOTER.size))
            index_size = mip_count * 4 + string_size + count * ENTRY.size
            if magic != b"HCXT" or version not in (6, 7) or pages * PAGE + index_size + FOOTER.size != size:
                raise ValueError(f"Unsupported texture cache format: {path}")
            source.seek(pages * PAGE)
            self.mips = source.read(mip_count * 4)
            strings = source.read(string_size)
            records = source.read(count * ENTRY.size)
        if checksum(self.mips, strings, records) != crc and checksum(strings, records) != crc:
            raise ValueError(f"Texture cache index checksum mismatch: {path}")
        self.version = version
        self.textures = []
        for fields in ENTRY.iter_unpack(records):
            name_offset, start_page, compressed_size = fields[1:4]
            end = strings.find(b"\0", name_offset)
            mip_index, mip_flags = fields[10:12]
            offsets = mip_flags & 0xffff
            if (
                not 0 <= name_offset < end
                or start_page * PAGE + compressed_size > pages * PAGE
                or (offsets and not 0 <= mip_index <= mip_count - offsets)
            ):
                raise ValueError(f"Invalid texture record in {path}")
            self.textures.append(Texture(strings[name_offset:end], fields))
        self.check_source()

    def file_stamp(self):
        info = self.path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    def check_source(self):
        if self.file_stamp() != self.stamp:
            raise ValueError(f"Texture cache changed after scanning: {self.path}. Scan again before exporting.")

    def write_without(self, output, removed):
        self.write_selected(
            output, [(self, texture) for texture in self.textures if texture.path not in removed],
            self.version,
        )

    @staticmethod
    def write_selected(output, entries, version):
        """Copy cooked payloads without decoding or recompressing their pixels."""
        if version not in (6, 7):
            raise ValueError("Unsupported output texture cache version.")
        entries = list(entries)
        paths = [texture.path for _, texture in entries]
        if len(paths) != len(set(paths)):
            raise ValueError("Choose only one texture source for each artwork path.")
        caches = {cache for cache, _ in entries}
        for cache in caches:
            cache.check_source()
        strings = bytearray()
        records = bytearray()
        mips = bytearray()
        with output.open("wb") as target:
            for cache, texture in entries:
                fields = list(texture.fields)
                fields[1] = len(strings)
                fields[2] = target.tell() // PAGE
                strings.extend(texture.name + b"\0")
                mip_index, mip_flags = fields[10:12]
                count = mip_flags & 0xffff
                if cache.version != version:
                    # v7 (next-gen) records carry 1 in the upper half of this field, v6 records 0;
                    # a v6 record copied unchanged into a v7 cache shows up as noise in game.
                    fields[11] = count | (V7_FLAG if version == 7 else 0)
                if count:
                    fields[10] = len(mips) // 4
                    mips.extend(cache.mips[mip_index * 4:(mip_index + count) * 4])
                records.extend(ENTRY.pack(*fields))
                with cache.path.open("rb") as source:
                    source.seek(texture.offset)
                    remaining = texture.size
                    while remaining:
                        chunk = source.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("Texture cache changed during export.")
                        target.write(chunk)
                        remaining -= len(chunk)
                target.write(bytes(-target.tell() % PAGE))
            pages = target.tell() // PAGE
            target.write(mips)
            target.write(strings)
            target.write(records)
            target.write(FOOTER.pack(
                checksum(mips, strings, records), pages, len(records) // ENTRY.size,
                len(strings), len(mips) // 4, b"HCXT", version,
            ))
        for cache in caches:
            cache.check_source()

    @staticmethod
    def write_images(output, images, version):
        """Write (template texture, RGBA image) pairs as single-mip raw RGBA (format 253) textures."""
        strings = bytearray()
        records = bytearray()
        with output.open("wb") as target:
            for template, image in images:
                raw = image.convert("RGBA").tobytes()
                compressed = zlib.compress(raw, 9)
                fields = list(template.fields)
                fields[1:12] = [
                    len(strings), target.tell() // PAGE, len(compressed) + 9, len(raw), 16,
                    image.width, image.height, 1, 1, 0, V7_FLAG if version == 7 else 0,
                ]
                fields[13:] = [253, 3, 0, 0]
                strings.extend(template.name + b"\0")
                records.extend(ENTRY.pack(*fields))
                target.write(struct.pack("<IIB", len(compressed), len(raw), 0) + compressed)
                target.write(bytes(-target.tell() % PAGE))
            pages = target.tell() // PAGE
            target.write(strings)
            target.write(records)
            target.write(FOOTER.pack(
                checksum(strings, records), pages, len(records) // ENTRY.size, len(strings), 0, b"HCXT", version,
            ))

    def preview(self, texture):
        from PIL import Image
        from PIL import DdsImagePlugin  # Register DDS for packaged builds.
        from io import BytesIO

        _, _, _, _, size, _, width, height, _, slices, _, _, _, fmt, _, cube, _ = texture.fields
        if slices != 1 or cube or not (0 < width <= 4096 and 0 < height <= 4096):
            raise ValueError("No preview available for this texture.")
        if size > 85 * 1024 * 1024:
            raise ValueError("Texture is too large to preview.")
        with self.path.open("rb") as source:
            source.seek(texture.offset)
            end = texture.offset + texture.size
            raw = bytearray()
            while source.tell() < end:
                compressed, unpacked, _ = struct.unpack("<IIB", source.read(9))
                if source.tell() + compressed > end or len(raw) + unpacked > size:
                    raise ValueError("Invalid compressed texture.")
                stream = zlib.decompressobj()
                chunk = stream.decompress(source.read(compressed), unpacked + 1)
                if len(chunk) != unpacked or not stream.eof:
                    raise ValueError("Invalid texture chunk.")
                raw.extend(chunk)
        if fmt == 253 and len(raw) >= width * height * 4:
            return Image.frombytes("RGBA", (width, height), bytes(raw[:width * height * 4]))
        if fmt in (6, 7, 8):
            fourcc = {6: b"DXT1", 7: b"DXT3", 8: b"DXT5"}[fmt]
            linear = ((width + 3) // 4) * ((height + 3) // 4) * (8 if fmt == 6 else 16)
            header = struct.pack("<7I", 124, 0x81007, height, width, linear, 0, 1)
            header += bytes(44) + struct.pack("<II4s5I", 32, 4, fourcc, 0, 0, 0, 0, 0)
            header += struct.pack("<5I", 0x1000, 0, 0, 0, 0)
            with Image.open(BytesIO(b"DDS " + header + raw[:linear]), formats=["DDS"]) as image:
                return image.convert("RGBA")
        raise ValueError(f"Preview unavailable for texture format {fmt}.")
