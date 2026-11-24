"""Localized Gwent card names from game, DLC and mod card definitions plus w3strings."""

from pathlib import Path, PurePosixPath
import re
import struct
import xml.etree.ElementTree as ET
import zlib


# (key1, key2) of each localized w3strings file -> string-table XOR magic.
STRING_KEYS = {
    (0x8349, 0x6237): 0x73946816,
    (0x4397, 0x5139): 0x79321793,
    (0x7588, 0x6138): 0x42791159,
    (0x4593, 0x1894): 0x12375973,
    (0x2386, 0x3176): 0x75921975,
    (0x2498, 0x7354): 0x21793217,
    (0x1879, 0x6651): 0x42387566,
    (0x1863, 0x2176): 0x16875467,
    (0x6348, 0x1486): 0x42386347,
    (0x4237, 0x8932): 0x67823218,
    (0x5483, 0x4893): 0x59825646,
}
CARD = re.compile(r"<card\b([^>]*)>", re.S)
ATTRIBUTE = re.compile(r'([\w:]+)\s*=\s*"([^"]*)"')


def lz4_block(data, size):
    output = bytearray()
    position = 0
    while position < len(data):
        token = data[position]
        position += 1
        length = token >> 4
        if length == 15:
            while True:
                extra = data[position]
                position += 1
                length += extra
                if extra != 255:
                    break
        output += data[position:position + length]
        position += length
        if position >= len(data):
            break
        offset = data[position] | data[position + 1] << 8
        position += 2
        if not offset or offset > len(output):
            raise ValueError("Corrupt LZ4 block.")
        length = token & 15
        if length == 15:
            while True:
                extra = data[position]
                position += 1
                length += extra
                if extra != 255:
                    break
        length += 4
        start = len(output) - offset
        if length <= offset:
            output += output[start:start + length]
        else:
            for index in range(length):
                output.append(output[start + index])
    if len(output) != size:
        raise ValueError("LZ4 size mismatch.")
    return bytes(output)


def decompress(data, size, method):
    if method == 0:
        return data
    if method == 1:
        return zlib.decompress(data)
    if method in (4, 5):
        return lz4_block(data, size)
    raise ValueError(f"Unsupported bundle compression {method}.")


def bundle_files(path, wanted):
    """Yield (asset name, bytes) for bundle entries whose lowercase name satisfies wanted()."""
    with Path(path).open("rb") as source:
        header = source.read(32)
        if len(header) != 32 or header[:8] != b"POTATO70":
            return
        length, version = struct.unpack_from("<IH", header, 16)
        stride = {3: 320, 5: 304}.get(version)
        if not stride or length % stride:
            return
        index = source.read(length)
        for start in range(0, length, stride):
            record = index[start:start + stride]
            name = record[:256].split(b"\0", 1)[0].decode("latin-1").replace("\\", "/").casefold()
            if not wanted(name):
                continue
            if version == 5:
                offset, size, packed, _, method = struct.unpack_from("<QIIII", record, 0x110)
            else:
                size, packed, offset = struct.unpack_from("<III", record, 0x114)
                method = struct.unpack_from("<I", record, 0x13C)[0]
            source.seek(offset)
            yield name, decompress(source.read(packed), size, method)


def string_hash(key):
    value = 0
    for character in key.casefold():
        value = (value * 31 + ord(character)) & 0xFFFFFFFF
    return value


def _bit6(data, position):
    value = shift = 0
    index = 1
    while True:
        byte = data[position]
        position += 1
        if byte > 127:
            mask, bits = 127, 7
        else:
            mask, bits = (63 if byte > 63 and index == 1 else 255), 6
        if shift < 32:
            value |= (byte & mask) << shift
        shift += bits
        if byte < 64 or (index >= 3 and byte < 128):
            return value, position
        index += 1


def read_strings(path, keys):
    """Return {key hash: text} for the requested key hashes in a w3strings file."""
    data = Path(path).read_bytes()
    if data[:4] != b"RTSW" or len(data) < 12:
        raise ValueError(f"Not a w3strings file: {path}")
    version, key1 = struct.unpack_from("<IH", data, 4)
    magic = STRING_KEYS.get((key1, struct.unpack_from("<H", data, len(data) - 2)[0]), 0)
    count, position = _bit6(data, 10)
    strings = {}
    for _ in range(count):
        identifier, offset, length = struct.unpack_from("<III", data, position)
        strings[identifier] = offset, length
        position += 12
    count, position = _bit6(data, position)
    wanted = {}
    for _ in range(count):
        key, identifier = struct.unpack_from("<II", data, position)
        if key in keys:
            wanted[key] = identifier
        position += 8
    _, base = _bit6(data, position)
    texts = {}
    for key, identifier in wanted.items():
        if identifier not in strings:
            continue
        offset, length = strings[identifier]
        if version >= 164:
            raw = bytearray(data[base + offset:base + offset + length])
            secret = (magic >> 8) & 0xFFFF
            for index in range(length if magic else 0):
                raw[index] ^= ((length + 1) * secret) & 0xFF
                secret = ((secret << 1) | (secret >> 15)) & 0xFFFF
            text = raw.decode("utf-8", "replace")
        else:
            raw = data[base + offset * 2:base + (offset + length) * 2]
            secret = (magic >> 8) & 0xFFFF
            characters = []
            for index in range(length):
                character = raw[index * 2] | raw[index * 2 + 1] << 8
                if magic:
                    character ^= ((length + 1) * secret) & 0xFFFF
                    secret = ((secret << 1) | (secret >> 15)) & 0xFFFF
                characters.append(chr(character))
            text = "".join(characters)
        texts[key] = text.strip()
    return texts


def card_records(text):
    """(title string key, lowercase picture name) for every card in a Gwent card definition XML."""
    records = []
    for attributes in card_details(text):
        picture, title = attributes.get("picture"), attributes.get("title")
        if picture and title:
            records.append((title.strip(), picture.strip().casefold()))
    return records


def card_details(text):
    """Return parsed attributes for every card element in a definition XML."""
    return [dict(ATTRIBUTE.findall(match[1])) for match in CARD.finditer(text)]


def card_titles(text):
    """Map lowercase picture names to title string keys from a Gwent card definition XML."""
    return {picture: title for title, picture in card_records(text)}


def definition_file(name):
    path = PurePosixPath(name)
    return path.suffix == ".xml" and path.name.startswith("def_gwint") and "items_plus" not in path.parts


def card_names(roots, language="en"):
    """Map picture names to localized titles; later roots override earlier ones."""
    return card_definitions(roots, language)[0]


def _source_name(root):
    if root.parent.name.casefold() == "mods":
        return f"Mods/{root.name}"
    if root.name.casefold() == "content":
        return "Game original"
    return f"Game DLC/{root.name}"


def _bundle_xml(path):
    return lambda name: definition_file(name) or name.endswith("gwent_faithful_artwork.xml")


def card_xml_catalog(roots, language="en"):
    """Collect selectable main card XMLs, active companion XMLs and Faithful DLC patches."""
    definitions = []
    patches = {}
    string_files = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        is_mod = root.parent.name.casefold() == "mods"
        for path in sorted(root.rglob("*")):
            suffix = path.suffix.casefold()
            try:
                found = []
                if suffix == ".bundle":
                    found = [
                        (name, data.decode("utf-8-sig", "replace"))
                        for name, data in bundle_files(path, _bundle_xml(path))
                    ]
                elif suffix == ".xml" and (
                    definition_file(path.as_posix().casefold())
                    or path.name.casefold() == "gwent_faithful_artwork.xml"
                ):
                    relative = path.relative_to(root).as_posix().casefold()
                    found = [(relative.split("content/", 1)[-1], path.read_text(encoding="utf-8-sig", errors="replace"))]
                elif suffix == ".w3strings" and path.stem.casefold() == language:
                    string_files.append(path)
                for name, text in found:
                    name = name.replace("\\", "/").casefold()
                    source = _source_name(root)
                    if name.endswith("gwent_faithful_artwork.xml"):
                        patches[name] = {
                            "asset": name,
                            "source": source,
                            "records": card_details(text),
                            "container": path.resolve() if suffix == ".bundle" else None,
                            "container_stamp": (
                                (path.stat().st_dev, path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
                                if suffix == ".bundle" else None
                            ),
                        }
                    elif definition_file(name):
                        definitions.append({
                            "id": f"{source}|{name}",
                            "label": f"{source} — {name}",
                            "source": source,
                            "asset": name,
                            "is_mod": is_mod,
                            "records": card_details(text),
                        })
            except (OSError, ValueError, struct.error, zlib.error, IndexError):
                continue

    title_keys = {
        string_hash(record["title"])
        for definition in definitions for record in definition["records"]
        if record.get("title")
    }
    title_keys.update(
        string_hash(record["title"])
        for patch in patches.values() for record in patch["records"]
        if record.get("title")
    )
    texts = {}
    for path in string_files:
        try:
            texts.update(read_strings(path, title_keys))
        except (OSError, ValueError, struct.error, IndexError):
            continue

    for definition in definitions:
        for record in definition["records"]:
            title = record.get("title")
            record["title_text"] = texts.get(string_hash(title)) if title else None
    for patch in patches.values():
        for record in patch["records"]:
            title = record.get("title")
            record["title_text"] = texts.get(string_hash(title)) if title else None

    winners = {}
    for definition in definitions:
        winners[definition["asset"]] = definition
    main_asset = next(
        (asset for asset in winners if PurePosixPath(asset).name == "def_gwint_cards_final.xml"),
        None,
    )
    options = [definition for definition in definitions if definition["asset"] == main_asset]
    default_main = options[-1]["id"] if options else None
    base_main = next((option for option in options if not option["is_mod"]), None)
    return {
        "definitions": definitions,
        "active": winners,
        "options": options,
        "default_main": default_main,
        "main_asset": main_asset,
        "base_main": base_main,
        "patches": list(patches.values()),
        "strings": texts,
    }


def selected_card_data(catalog, main_xml_id=None):
    """Return localized names, used pictures and title-based links for a selected main XML."""
    active = dict(catalog["active"])
    selected = next(
        (option for option in catalog["options"] if option["id"] == main_xml_id),
        None,
    ) or next((option for option in catalog["options"] if option["id"] == catalog["default_main"]), None)
    if selected and catalog["main_asset"]:
        active[catalog["main_asset"]] = selected
    all_records = [record for definition in active.values() for record in definition["records"]]
    names, used = {}, set()
    for record in all_records:
        picture = record.get("picture")
        if not picture:
            continue
        picture = picture.casefold()
        used.add(picture)
        if record.get("title_text"):
            names[picture] = record["title_text"]

    original = {}
    for definition in catalog["definitions"]:
        if definition["is_mod"]:
            continue
        for record in definition["records"]:
            if record.get("title") and record.get("picture"):
                original.setdefault(record["title"].casefold(), record["picture"].casefold())
    links = {}
    for record in all_records:
        title, picture = record.get("title"), record.get("picture")
        old = original.get(title.casefold()) if title else None
        if old and picture and old != picture.casefold():
            links.setdefault(picture.casefold(), old)
    return names, used or None, links


def card_definitions(roots, language="en"):
    """Return (names, used pictures, picture links); the winning root comes last."""
    catalog = card_xml_catalog(roots, language)
    return selected_card_data(catalog)


def faithful_xml_overrides(catalog, main_xml_id=None):
    """Rewrite Faithful artwork patches to the selected card XML's names and picture paths."""
    if not catalog or not catalog["patches"]:
        return {}, (0, 0, 0)
    selected = next(
        (option for option in catalog["options"] if option["id"] == main_xml_id),
        None,
    ) or next((option for option in catalog["options"] if option["id"] == catalog["default_main"]), None)
    if not selected:
        return {}, (0, sum(len(patch["records"]) for patch in catalog["patches"]), 0)

    records = selected["records"]
    by_name = {}
    by_title = {}
    for record in records:
        if record.get("name"):
            by_name.setdefault(record["name"].casefold(), []).append(record)
        if record.get("title_text"):
            by_title.setdefault(record["title_text"].casefold(), []).append(record)
    base_by_name = {}
    if catalog["base_main"]:
        for record in catalog["base_main"]["records"]:
            if record.get("name"):
                base_by_name.setdefault(record["name"].casefold(), []).append(record)

    outputs = {}
    patch_total = len(next(iter(catalog["patches"]))["records"])
    mapped_count = 0
    for patch in catalog["patches"]:
        output = []
        seen = set()
        for source in patch["records"]:
            target = None
            if source.get("title"):
                if source.get("title_text"):
                    candidates = by_title.get(source["title_text"].casefold(), [])
                    if len(candidates) == 1:
                        target = candidates[0]
            if target is None and source.get("name"):
                candidates = by_name.get(source["name"].casefold(), [])
                base = base_by_name.get(source["name"].casefold(), [])
                if len(candidates) == 1 and (
                    not base or (len(base) == 1 and base[0].get("title") == candidates[0].get("title"))
                ):
                    target = candidates[0]
            if not target or not target.get("picture") or not target.get("name"):
                continue
            key = target["name"].casefold()
            if key in seen:
                continue
            seen.add(key)
            attributes = {
                "name": target["name"],
                "on_conflict": "extend",
                "picture": target["picture"],
            }
            if target.get("title"):
                attributes["title"] = target["title"]
            if target.get("dlcPicture"):
                attributes["dlcPicture"] = target["dlcPicture"]
            output.append(attributes)
        mapped_count = max(mapped_count, len(output))
        root = ET.Element("redxml")
        definitions = ET.SubElement(root, "definitions")
        cards = ET.SubElement(definitions, "gwint_card_definitions_final")
        for attributes in output:
            ET.SubElement(cards, "card", attributes)
        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        outputs[patch["asset"]] = data
    return outputs, (patch_total, mapped_count, patch_total - mapped_count)
