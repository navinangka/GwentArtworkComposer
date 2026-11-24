"""Write a small Witcher 3 bundle and matching metadata index."""

import struct
import zlib


MAGIC = b"POTATO70"
LEGACY_HEADER = b"\x03\x00\x01\x00\x00\x13\x13\x13\x13\x13\x13\x13"


def _vlq(value):
    negative = value <= 0
    value = abs(value)
    first = value & 0x3f
    value >>= 6
    if negative:
        first |= 0x80
    if value:
        first |= 0x40
    result = bytearray((first,))
    while value:
        byte = value & 0x7f
        value >>= 7
        if value:
            byte |= 0x80
        result.append(byte)
    return bytes(result)


def write_bundle(files, version=5):
    """Pack {depot_path: bytes} as a single-file-layout legacy or remastered bundle."""
    if version not in (3, 5):
        raise ValueError(f"Unsupported bundle version: {version}")
    files = sorted(files.items())
    if not files:
        raise ValueError("A bundle must contain at least one asset.")
    names = [name.replace("/", "\\") for name, _ in files]
    if len(set(name.casefold() for name in names)) != len(names):
        raise ValueError("Bundle asset paths must be unique.")
    if any(not name or len(name.encode("latin-1")) > 255 for name in names):
        raise ValueError("Bundle asset paths must fit in the 255-byte name field.")

    entry_size = 304 if version == 5 else 320
    data_start = 32 + entry_size * len(files)
    payloads, records = bytearray(), []
    for (asset, data), name in zip(files, names):
        compressed = zlib.compress(data)
        if version == 5:
            offset = data_start + len(payloads)
            record = (
                name.encode("latin-1") + bytes(256 - len(name.encode("latin-1")))
                + bytes(16) + struct.pack("<QIIII", offset, len(data), len(compressed),
                                           zlib.crc32(data), 1)
                + bytes(8)
            )
        else:
            offset = data_start + len(payloads)
            record = (
                name.encode("latin-1") + bytes(256 - len(name.encode("latin-1")))
                + bytes(16) + struct.pack("<IIIII", 0, len(data), len(compressed), offset, 0)
                + struct.pack("<I", 0) + bytes(16) + struct.pack("<II", zlib.crc32(data), 1)
            )
        if len(record) != entry_size:
            raise ValueError("Invalid bundle table entry size.")
        records.append((name, data, compressed, offset))
        payloads.extend(compressed)

    table = b"".join(
        name.encode("latin-1") + bytes(256 - len(name.encode("latin-1")))
        + record[256:]
        for name, record in zip(names, _bundle_records(records, version))
    )
    if version == 5:
        header = MAGIC + struct.pack("<QI H Q", data_start + len(payloads), len(table), 5, data_start) + bytes(2)
    else:
        header = (
            MAGIC + struct.pack("<IIIH", data_start + len(payloads), 0, len(table), 3)
            + LEGACY_HEADER[2:]
        )
    return header + table + payloads, records, data_start


def _bundle_records(records, version):
    """Return serialized TOC records; kept separate to share exact offsets with metadata."""
    for name, data, compressed, offset in records:
        encoded = name.encode("latin-1")
        if version == 5:
            yield (
                encoded + bytes(256 - len(encoded)) + bytes(16)
                + struct.pack("<QIIII", offset, len(data), len(compressed), zlib.crc32(data), 1)
                + bytes(8)
            )
        else:
            yield (
                encoded + bytes(256 - len(encoded)) + bytes(16)
                + struct.pack("<IIIII", 0, len(data), len(compressed), offset, 0)
                + struct.pack("<I", 0) + bytes(16) + struct.pack("<II", zlib.crc32(data), 1)
            )


def write_metadata(bundle_name, files, records, data_start, version=7):
    """Build a metadata.store for the emitted single bundle."""
    if version not in (6, 7):
        raise ValueError(f"Unsupported metadata version: {version}")
    if len(files) != len(records):
        raise ValueError("Bundle metadata entries do not match the files.")

    strings = bytearray(b"\0")
    offsets = {b"": 0}

    def intern(value):
        raw = value.encode("latin-1")
        if raw not in offsets:
            offsets[raw] = len(strings)
            strings.extend(raw + b"\0")
        return offsets[raw]

    file_offsets = [intern(name.replace("/", "\\")) for name, _ in sorted(files.items())]
    bundle_offset = intern(bundle_name)
    directories = [(0, 0)]
    directory_ids = {(): 0}
    file_init, file_info, entry_info = [], [bytes(32)], [bytes(24 if version == 7 else 20)]
    for file_id, ((name, data), record) in enumerate(zip(sorted(files.items()), records), 1):
        path = name.replace("/", "\\")
        components = path.split("\\")
        current = ()
        for component in components[:-1]:
            parent = directory_ids[current]
            current += (component,)
            if current not in directory_ids:
                directory_ids[current] = len(directories)
                directories.append((intern(component), parent))
        directory_id = directory_ids[current]
        leaf_offset = intern(components[-1])
        _asset, raw, compressed, offset = record
        entry_id = len(entry_info)
        if version == 7:
            entry_info.append(struct.pack("<Q4I", offset, len(compressed), 0, file_id, 1))
        else:
            entry_info.append(struct.pack("<5I", file_id, 1, offset, len(compressed), 0))
        file_info.append(struct.pack(
            "<8I", file_offsets[file_id - 1], 0, len(compressed), len(raw), entry_id, 1, 0, 0,
        ))
        file_init.append(struct.pack("<3i", file_id, directory_id, leaf_offset))

    directory_data = b"".join(struct.pack("<2i", name_offset, parent) for name_offset, parent in directories)
    bundle_path_size = sum(len(compressed) for _, _, compressed, _ in records)
    if version == 7:
        bundle_record = struct.pack("<Q4I", bundle_path_size, data_start, bundle_offset, 1, len(records))
    else:
        bundle_record = struct.pack("<6I", bundle_offset, 1, len(records), bundle_path_size, data_start, 0)
    bundle_infos = [bytes(24), bundle_record]
    arrays = [
        _array(file_info),
        _array(entry_info),
        _array(bundle_infos),
        _array([]),
        _array(directory_data, len(directories)),
        _array(file_init),
        _array([]),
    ]
    max_bundle = max((len(compressed) for _, _, compressed, _ in records), default=0)
    max_memory = max((len(data) for _, data in sorted(files.items())), default=0)
    header = b"\x03VTM" + struct.pack("<3I", version, max_bundle, max_memory)
    return header + _vlq(len(strings)) + strings + b"".join(arrays)


def _array(records, count=None):
    if count is None:
        count = len(records)
        data = b"".join(records)
    else:
        data = records
    return _vlq(count) + data
