from pathlib import Path
from types import SimpleNamespace
import struct
import tempfile
import tkinter as tk
import unittest
from zipfile import ZipFile
import zlib

from PIL import Image

from app import App
from bundle import write_bundle, write_metadata
from cache import ENTRY, FOOTER, PAGE, TextureCache, card_faction, checksum
from app import ACCENT
from composer import ArtworkLibrary, MOD_NAME, NONE, ORIGINAL, UPLOAD, alternate_base, empty_metadata
from mod import Artwork, GameArtwork, parse_load_order


NOR = "gameplay/gui_new/icons/gwint/nor_test.png"
NEU = "gameplay/gui_new/icons/gwint/neu_test.png"
SKE = "dlc/bob/data/gameplay/gui_new/icons/gwint/ske_test.png"
NOR_ALT = "gameplay/gui_new/icons/gwint/nor_test_alt1.png"
NOR_ALT2 = "gameplay/gui_new/icons/gwint/nor_test_alt2.png"
NOR_ALT10 = "gameplay/gui_new/icons/gwint/nor_test_alt10.png"
DLC_ALT = "dlc/dlc8/data/gameplay/gui_new/icons/gwint/nor_test_alt1.png"


def make_cache(path, specs, version=7):
    payloads = bytearray()
    strings = bytearray()
    records = bytearray()
    mips = bytearray()
    for key, raw, width, height, fmt in specs:
        compressed = zlib.compress(raw)
        payload = struct.pack("<IIB", len(compressed), len(raw), 0) + compressed
        record = ENTRY.pack(
            zlib.crc32(key.encode()), len(strings), len(payloads) // PAGE, len(payload),
            len(raw), 16, width, height, 1, 1, len(mips) // 4, 1 | (0x10000 if version == 7 else 0),
            12345, fmt, 3, 0, 0,
        )
        mips.extend(struct.pack("<I", 0))
        strings.extend(key.replace("/", "\\").encode() + b"\0")
        records.extend(record)
        payloads.extend(payload)
        payloads.extend(bytes(-len(payloads) % PAGE))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        payloads + mips + strings + records
        + FOOTER.pack(checksum(mips, strings, records), len(payloads) // PAGE,
                      len(specs), len(strings), len(mips) // 4, b"HCXT", version)
    )
    return TextureCache(path)


def provider(root, cache):
    relative = Path("content") / "texture.cache"
    return SimpleNamespace(
        root=root, artwork={texture.path: [Artwork(relative, texture)] for texture in cache.textures},
        bundled=set(), caches={relative: cache},
    )


def sample_library(folder):
    root = Path(folder)
    game_cache = make_cache(root / "game" / "content" / "texture.cache", [
        (NOR, bytes((220, 150, 50, 255)), 1, 1, 253),
        (NEU, bytes((40, 60, 80, 255)), 1, 1, 253),
    ])
    first_cache = make_cache(root / "first" / "content" / "texture.cache", [
        (NOR, bytes((230, 120, 30, 128)), 1, 1, 253),
        (NEU, bytes((10, 60, 200, 255)), 1, 1, 253),
        ("characters/unrelated.png", bytes((1, 2, 3, 4)), 1, 1, 253),
    ])
    second_cache = make_cache(root / "second" / "content" / "texture.cache", [
        (NOR, struct.pack("<HHI", 0xf800, 0, 0), 4, 4, 6),
        (SKE, bytes((90, 70, 50, 255)), 1, 1, 253),
    ], version=6)
    game = object.__new__(GameArtwork)
    game.root = root / "game"
    game.artwork = {texture.path: (game_cache, texture) for texture in game_cache.textures}
    game.bundled = set()
    first = provider(root / "first", first_cache)
    first.artwork.pop("characters/unrelated.png")
    second = provider(root / "second", second_cache)
    return ArtworkLibrary(game, {"First mod": first, "Second mod": second})


def with_alternates(library, folder):
    root = Path(folder) / "alternates"
    cache = make_cache(root / "content" / "texture.cache", [
        (NOR_ALT2, bytes((1, 2, 3, 255)), 1, 1, 253),
        (NOR_ALT10, bytes((10, 11, 12, 255)), 1, 1, 253),
        (NOR_ALT, bytes((4, 5, 6, 255)), 1, 1, 253),
        (DLC_ALT, bytes((7, 8, 9, 255)), 1, 1, 253),
    ])
    mods = {label: mod for label, mod in library.providers.items() if label != ORIGINAL}
    mods["Alternate mod"] = provider(root, cache)
    return ArtworkLibrary(library.game, mods)


class ComposerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.library = sample_library(self.folder)

    def tearDown(self):
        self.temporary.cleanup()

    def test_uncompressed_rgba_and_dxt_color_order(self):
        self.assertEqual(self.library.preview(NOR, "First mod").getpixel((0, 0)), (230, 120, 30, 128))
        self.assertEqual(self.library.preview(NEU, "First mod").getpixel((0, 0)), (10, 60, 200, 255))
        self.assertEqual(self.library.preview(NOR, "Second mod").getpixel((0, 0)), (255, 0, 0, 255))

    def test_mixed_zip_contains_exact_choices_and_preserves_payloads_and_mips(self):
        self.library.choose([NEU], "First mod")
        self.library.choose([NOR, SKE], "Second mod")
        destination = self.folder / "artwork.zip"
        before = {cache.path: cache.path.read_bytes() for mod in self.library.providers.values()
                  if hasattr(mod, "caches") for cache in mod.caches.values()}
        self.library.export(destination)
        with ZipFile(destination) as archive:
            prefix = f"Mods/{MOD_NAME}/content/"
            self.assertEqual(set(archive.namelist()), {prefix + "texture.cache", prefix + "metadata.store"})
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read(prefix + "metadata.store"), empty_metadata(7))
            cache_path = self.folder / "exported.cache"
            cache_path.write_bytes(archive.read(prefix + "texture.cache"))
        repacked = TextureCache(cache_path)
        self.assertEqual(repacked.version, 7)
        self.assertEqual({texture.path for texture in repacked.textures}, {NOR, NEU, SKE})
        for texture in repacked.textures:
            mod = self.library.providers[self.library.chosen_source(texture.path)]
            art = mod.artwork[texture.path][0]
            original = mod.caches[art.file]
            source_bytes = original.path.read_bytes()[art.texture.offset:art.texture.offset + art.texture.size]
            target_bytes = cache_path.read_bytes()[texture.offset:texture.offset + texture.size]
            self.assertEqual(target_bytes, source_bytes)
            self.assertEqual(texture.fields[12:], art.texture.fields[12:])
            # SKE/NOR come from a v6 cache: the v7 output must carry the v7 record flag (else noise in game).
            self.assertEqual(texture.fields[11], 0x10001)
            self.assertEqual(repacked.mips[texture.fields[10] * 4: texture.fields[10] * 4 + 4], b"\0" * 4)
            self.assertEqual(repacked.preview(texture).tobytes(),
                             self.library.preview(texture.path, self.library.chosen_source(texture.path)).tobytes())
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def exported(self, library, name):
        library.export(self.folder / f"{name}.zip")
        with ZipFile(self.folder / f"{name}.zip") as archive:
            data = archive.read(f"Mods/{MOD_NAME}/content/texture.cache")
        path = self.folder / f"{name}.cache"
        path.write_bytes(data)
        return TextureCache(path)

    def test_every_card_starts_as_no_texture_and_originals_are_packed_when_chosen(self):
        self.assertEqual({self.library.chosen_source(key) for key in self.library.paths}, {NONE})
        with self.assertRaisesRegex(ValueError, "Nothing to export"):
            self.library.export(self.folder / "empty.zip")
        self.library.choose([NOR, NEU], ORIGINAL)
        cache = self.exported(self.library, "originals")
        self.assertEqual({texture.path for texture in cache.textures}, {NOR, NEU})
        game_cache, texture = self.library.game.artwork[NOR]
        exported = next(item for item in cache.textures if item.path == NOR)
        self.assertEqual(cache.path.read_bytes()[exported.offset:exported.offset + exported.size],
                         game_cache.path.read_bytes()[texture.offset:texture.offset + texture.size])
        self.library.choose([NOR, NEU], NONE)
        self.library.choose([SKE], "Second mod")
        self.assertEqual([texture.path for texture in self.exported(self.library, "one").textures], [SKE])

    def test_uploaded_image_is_center_cropped_to_card_size_and_exported(self):
        image_path = self.folder / "upload.png"
        image = Image.new("RGBA", (300, 100), (255, 0, 0, 255))
        image.paste((0, 255, 0, 255), (80, 0, 220, 100))
        image.save(image_path)
        with self.assertRaisesRegex(ValueError, "Upload an image"):
            self.library.choose([NOR], UPLOAD)
        self.library.upload([NOR, SKE], image_path)
        self.assertEqual(self.library.choices, {NOR: UPLOAD, SKE: UPLOAD})
        self.assertEqual(self.library.uploads[NOR].size, (1, 1))
        self.assertEqual(self.library.uploads[NOR].getpixel((0, 0)), (0, 255, 0, 255))
        cache = self.exported(self.library, "upload")
        textures = {texture.path: texture for texture in cache.textures}
        self.assertEqual(set(textures), {NOR, SKE})
        game_texture = self.library.game.artwork[NOR][1]
        self.assertEqual(textures[NOR].fields[0], game_texture.fields[0])
        self.assertEqual(textures[NOR].fields[6:8], (1, 1))
        self.assertEqual(cache.preview(textures[NOR]).getpixel((0, 0)), (0, 255, 0, 255))
        self.library.choose([NOR], "First mod")
        self.library.choose([NOR], UPLOAD)
        self.assertEqual(self.library.chosen_source(NOR), UPLOAD)

    def test_png_export_uses_chosen_sources_and_skips_no_texture(self):
        self.library.choose([NEU], "First mod")
        self.library.choose([SKE], "Second mod")
        self.library.choose([NOR], NONE)
        count, errors = self.library.export_png(self.library.export_keys(), folder=self.folder / "png")
        self.assertEqual((count, errors), (2, []))
        files = {path.relative_to(self.folder / "png").as_posix() for path in (self.folder / "png").rglob("*.png")}
        self.assertEqual(files, {NEU, SKE})
        with Image.open(self.folder / "png" / NEU) as image:
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (10, 60, 200, 255))
        single = self.folder / "one.png"
        self.assertEqual(self.library.export_png([NEU, NOR], single=single), (1, []))
        self.assertTrue(single.is_file())
        with self.assertRaisesRegex(ValueError, "No texture"):
            self.library.export_png([NOR], folder=self.folder)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.library.export_png([NEU], folder=self.library.game.root)

    def test_load_order_reference_follows_priority(self):
        order = parse_load_order(
            "[modFirst]\nEnabled=1\nPriority=20\n[modSecond]\nEnabled=1\nPriority=5\n"
            "[modOff]\nEnabled=0\nPriority=1\n"
        )
        self.assertEqual(order, {"modfirst": (True, 20), "modsecond": (True, 5), "modoff": (False, 1)})
        mods = {label: mod for label, mod in self.library.providers.items() if label != ORIGINAL}
        mods["First mod"].load_name, mods["Second mod"].load_name = "modFirst", "modSecond"
        library = ArtworkLibrary(self.library.game, mods, order)
        self.assertEqual(list(library.providers), [ORIGINAL, "Second mod", "First mod"])
        self.assertEqual(library.load_order_defaults, {NOR: "Second mod", NEU: "First mod", SKE: "Second mod"})
        self.assertEqual(library.chosen_source(NOR), NONE)
        library.choose([NOR], ORIGINAL)
        self.assertEqual(library.choices, {NOR: ORIGINAL})
        library.choose([NOR], NONE)
        self.assertEqual(library.choices, {})
        mods["Second mod"].load_name = "modOff"
        library = ArtworkLibrary(self.library.game, mods, order)
        self.assertEqual(library.load_order_defaults[NOR], "First mod")
        self.assertEqual(library.load_order_defaults[SKE], NONE)
        mods["First mod"].load_name = "modUnlisted"
        library = ArtworkLibrary(self.library.game, mods, order)
        self.assertEqual(library.load_order_defaults[NOR], "First mod")

    def test_unavailable_batch_selection_is_rejected_atomically(self):
        with self.assertRaisesRegex(ValueError, "every selected"):
            self.library.choose([NOR, NEU], "Second mod")
        self.assertEqual(self.library.choices, {})

    def test_source_changes_preserve_existing_destination(self):
        self.library.choose([NOR], "First mod")
        destination = self.folder / "existing.zip"
        destination.write_bytes(b"previous export")
        source = self.library.providers["First mod"].caches[Path("content") / "texture.cache"].path
        with source.open("ab") as output:
            output.write(b"changed")
        with self.assertRaisesRegex(ValueError, "changed after scanning"):
            self.library.export(destination)
        self.assertEqual(destination.read_bytes(), b"previous export")

    def test_duplicate_cache_paths_and_game_destination_are_rejected(self):
        cache, texture = self.library.game.artwork[NOR]
        with self.assertRaisesRegex(ValueError, "only one"):
            TextureCache.write_selected(self.folder / "duplicate.cache", [(cache, texture)] * 2, 7)
        self.library.choose([NOR], "First mod")
        with self.assertRaisesRegex(ValueError, "outside"):
            self.library.export(self.library.game.root / "invalid.zip")

    def test_alternate_groups_keep_paths_distinct_and_export_only_clicked_alternate(self):
        library = with_alternates(self.library, self.folder)
        self.assertEqual(library.groups[NOR], [NOR, NOR_ALT, NOR_ALT2, NOR_ALT10])
        dlc_base = "dlc/dlc8/data/gameplay/gui_new/icons/gwint/nor_test.png"
        self.assertEqual(library.groups[dlc_base], [DLC_ALT])
        self.assertEqual(alternate_base("gameplay/gui_new/icons/gwint/neu_geralt_dlc.png"),
                         "gameplay/gui_new/icons/gwint/neu_geralt_dlc.png")
        library.choose([NOR_ALT], "Alternate mod")
        exported = self.exported(library, "alternate")
        self.assertEqual({texture.path for texture in exported.textures}, {NOR_ALT})
        self.assertEqual(library.chosen_source(NOR), NONE)
        self.assertEqual(library.chosen_source(NOR_ALT2), NONE)
        self.assertEqual(library.chosen_source(DLC_ALT), NONE)

    def test_project_round_trip_restores_choices_uploads_and_maps_moved_sources(self):
        image_path = self.folder / "upload.png"
        Image.new("RGBA", (3, 3), (0, 0, 255, 255)).save(image_path)
        self.library = with_alternates(self.library, self.folder)
        self.library.choose([NEU], "First mod")
        self.library.choose([SKE], "Second mod")
        self.library.upload([NOR], image_path)
        kept = NOR_ALT
        self.library.choose([kept], self.library.defaults[kept])
        self.assertNotIn(kept, self.library.choices)
        self.assertEqual(self.library.edited, {NEU, SKE, NOR, kept})
        project = self.folder / "test.gwentproj"
        self.library.save_project(project)

        fresh = with_alternates(sample_library(self.folder), self.folder)
        fresh.providers["Renamed"] = fresh.providers.pop("Second mod")
        fresh.available[SKE] = ("Renamed",)
        choices, uploads, warnings, main_xml, matches = fresh.read_project(project)
        self.assertEqual(warnings, [])
        self.assertIsNone(main_xml)
        self.assertEqual(matches, {})
        self.assertEqual(fresh.choices, {})
        fresh.apply_project(choices, uploads)
        self.assertEqual(fresh.choices, {NEU: "First mod", SKE: "Renamed", NOR: UPLOAD})
        self.assertEqual(fresh.edited, {NEU, SKE, NOR, kept})
        self.assertEqual(fresh.uploads[NOR].getpixel((0, 0)), (0, 0, 255, 255))

        del fresh.providers["Renamed"]
        fresh.available[SKE] = ()
        choices, _, warnings, _, _ = fresh.read_project(project)
        self.assertNotIn(SKE, choices)
        self.assertEqual(len(warnings), 1)
        (self.folder / "bad.gwentproj").write_bytes(b"not a zip")
        with self.assertRaisesRegex(ValueError, "Could not read project"):
            fresh.read_project(self.folder / "bad.gwentproj")

    def test_removing_a_source_resets_only_its_choices(self):
        self.library.choose([NOR], "Second mod")
        self.library.choose([NEU], "First mod")
        self.library.choose([SKE], "Second mod")
        library, reset = self.library.without_source("Second mod")
        self.assertEqual(list(library.providers), [ORIGINAL, "First mod"])
        self.assertEqual(reset, sorted([NOR, SKE]))
        self.assertNotIn(SKE, library.available)
        self.assertEqual(library.chosen_source(NOR), library.defaults[NOR])
        self.assertEqual(library.chosen_source(NEU), "First mod")
        self.assertEqual(library.edited, {NEU})
        with self.assertRaises(ValueError):
            library.without_source(ORIGINAL)

    def test_bulk_import_matches_asset_paths_then_file_names(self):
        library = with_alternates(self.library, self.folder)
        folder = self.folder / "import"
        exact = folder / Path(DLC_ALT).with_suffix(".bmp")
        exact.parent.mkdir(parents=True)
        Image.new("RGB", (4, 2), (255, 0, 0)).save(exact)
        Image.new("RGBA", (2, 2), (0, 255, 0, 255)).save(folder / "NOR_TEST_ALT1.png")
        Image.new("RGBA", (2, 2), (0, 0, 255, 255)).save(folder / "neu_test.webp")
        (folder / "unknown_card.png").write_bytes((folder / "NOR_TEST_ALT1.png").read_bytes())
        images, unmatched, errors = library.prepare_import([folder])
        self.assertEqual(errors, [])
        self.assertEqual([Path(path).name for path in unmatched], ["unknown_card.png"])
        self.assertEqual(set(images), {DLC_ALT, NOR_ALT, NEU})
        self.assertEqual(images[DLC_ALT].getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(images[NOR_ALT].getpixel((0, 0)), (0, 255, 0, 255))
        self.assertEqual(library.choices, {})
        library.apply_uploads(images)
        self.assertEqual({key: library.chosen_source(key) for key in images}, dict.fromkeys(images, UPLOAD))
        self.assertEqual(set(library.prepare_import([folder / "neu_test.webp"])[0]), {NEU})

    def test_card_names_come_from_lz4_bundle_definitions_and_encrypted_strings(self):
        from names import card_definitions, card_names, lz4_block, string_hash

        self.assertEqual(lz4_block(b"\x44abcd\x04\x00\x10z", 13), b"abcdabcdabcdz")
        game = self.folder / "names" / "content"
        game.mkdir(parents=True)
        xml = (
            '\ufeff<redxml><definitions><gwint_card_definitions_final>'
            '<card index="1" title="gwint_name_test"\n picture="nor_test" faction_index="F_NORTHERN_KINGDOM">'
            '</card></gwint_card_definitions_final></definitions></redxml>'
        ).encode("utf-8")
        extra = len(xml) - 15
        literal = b"\xf0" + b"\xff" * (extra // 255) + bytes([extra % 255]) + xml
        name = b"gameplay\\items\\def_gwint_cards_final.xml"
        record = bytearray(320)
        record[:len(name)] = name
        struct.pack_into("<III", record, 0x114, len(xml), len(literal), 32 + 320)
        struct.pack_into("<I", record, 0x13C, 5)
        (game / "blob0.bundle").write_bytes(
            b"POTATO70" + struct.pack("<IIIH", 0, 0, 320, 3) + bytes(10) + record + literal
        )
        magic, text = 0x79321793, "Blue Stripes Commando".encode()
        encrypted, secret = bytearray(text), (magic >> 8) & 0xFFFF
        for index in range(len(text)):
            encrypted[index] ^= ((len(text) + 1) * secret) & 0xFF
            secret = ((secret << 1) | (secret >> 15)) & 0xFFFF
        (game / "en.w3strings").write_bytes(
            b"RTSW" + struct.pack("<IH", 164, 0x4397)
            + b"\x01" + struct.pack("<III", 7 ^ magic, 0, len(text))
            + b"\x01" + struct.pack("<II", string_hash("GWINT_NAME_TEST"), 7 ^ magic)
            + bytes([len(text) + 1]) + encrypted + b"\0" + struct.pack("<H", 0x5139)
        )
        self.assertEqual(card_names([game]), {"nor_test": "Blue Stripes Commando"})
        library = with_alternates(self.library, self.folder)
        library.names = card_names([game])
        self.assertEqual(library.card_name(NOR), "Blue Stripes Commando")
        self.assertEqual(library.card_name(NOR_ALT2), "Blue Stripes Commando (alt 2)")
        self.assertEqual(library.card_name(NOR.replace("nor_test", "nor_test_dlc")), "Blue Stripes Commando")
        self.assertIsNone(library.card_name(NEU))
        mod = self.folder / "names" / "Mods" / "modRedux"
        (mod / "content" / "gameplay" / "items").mkdir(parents=True)
        (mod / "content" / "gameplay" / "items" / "def_gwint_cards_final.xml").write_text(
            '<redxml><card index="1" title="GWINT_NAME_TEST" picture="nor_moved"></card></redxml>', encoding="utf-8",
        )
        names, used, links = card_definitions([game, mod])
        self.assertEqual(used, {"nor_moved"})
        self.assertEqual(links, {"nor_moved": "nor_test"})
        self.assertEqual(card_definitions([game])[1:], ({"nor_test"}, {}))

    def test_main_xml_selection_and_faithful_override_preserve_only_matching_cards(self):
        from names import card_xml_catalog, faithful_xml_overrides, selected_card_data
        import xml.etree.ElementTree as ET

        base = self.folder / "game" / "content"
        redux = self.folder / "game" / "Mods" / "modRedux"
        faithful = self.folder / "game" / "DLC" / "dlcgwentfaithfulartwork"
        asset = Path("content") / "gameplay" / "items" / "def_gwint_cards_final.xml"
        original_xml = (
            '<redxml><definitions><gwint_card_definitions_final>'
            '<card name="alpha" title="title_alpha" picture="nor_alpha"/>'
            '<card name="beta" title="title_beta" picture="nil_beta"/>'
            '</gwint_card_definitions_final></definitions></redxml>'
        )
        redux_xml = original_xml.replace('picture="nor_alpha"', 'picture="neu_alpha"').replace(
            'title="title_beta"', 'title="title_other"'
        ).replace('picture="nil_beta"', 'picture="nml_beta"')
        (base / asset).parent.mkdir(parents=True)
        (base / asset).write_text(original_xml, encoding="utf-8")
        (redux / asset).parent.mkdir(parents=True)
        (redux / asset).write_text(redux_xml, encoding="utf-8")
        patch_asset = (
            Path("content") / "dlc" / "dlcgwentfaithfulartwork" / "data"
            / "gameplay" / "items" / "gwent_faithful_artwork.xml"
        )
        patch_path = faithful / patch_asset
        patch_path.parent.mkdir(parents=True)
        patch_path.write_text(
            '<redxml><definitions><gwint_card_definitions_final>'
            '<card name="alpha" on_conflict="extend" picture="faithful_alpha"/>'
            '<card name="beta" on_conflict="extend" picture="faithful_beta"/>'
            '</gwint_card_definitions_final></definitions></redxml>',
            encoding="utf-8",
        )

        catalog = card_xml_catalog([base, faithful, redux])
        self.assertEqual(len(catalog["options"]), 2)
        self.assertEqual(catalog["options"][-1]["source"], "Mods/modRedux")
        _, used, _ = selected_card_data(catalog)
        self.assertEqual(used, {"neu_alpha", "nml_beta"})
        overrides, stats = faithful_xml_overrides(catalog)
        self.assertEqual(stats, (2, 1, 1))
        [patched] = overrides.values()
        cards = ET.fromstring(patched).findall(".//card")
        self.assertEqual([(card.get("name"), card.get("picture")) for card in cards], [("alpha", "neu_alpha")])
        mods = {label: mod for label, mod in self.library.providers.items() if label != ORIGINAL}
        library = ArtworkLibrary(self.library.game, mods, xml_catalog=catalog)
        destination = self.folder / "xml-only.zip"
        library.export(destination)
        with ZipFile(destination) as archive:
            prefix = f"Mods/{MOD_NAME}/content/"
            self.assertEqual(set(archive.namelist()), {prefix + "metadata.store", prefix + "blob0.bundle"})

    def test_card_moved_by_mod_xml_borrows_old_path_textures_and_exports_under_new_path(self):
        from cache import name_hash

        moved = NOR.replace("nor_test", "nor_moved")
        library = ArtworkLibrary(
            self.library.game, {label: mod for label, mod in self.library.providers.items() if label != ORIGINAL},
            links={"nor_moved": "nor_test"}, used={"nor_moved", "neu_test", "ske_test"},
        )
        self.assertIn(moved, library.paths)
        self.assertEqual(library.available[moved], (ORIGINAL, "First mod", "Second mod"))
        self.assertEqual(library.load_order_defaults[moved], NONE)
        self.assertFalse(library.in_use(NOR))
        self.assertTrue(library.in_use(moved) and library.in_use(NEU))
        self.assertEqual(library.linked_from(moved, "First mod"), NOR)
        self.assertEqual(library.preview(moved, "First mod").getpixel((0, 0)), (230, 120, 30, 128))
        library.choose([moved], "First mod")
        cache = self.exported(library, "moved")
        [texture] = cache.textures
        self.assertEqual(texture.path, moved)
        self.assertEqual(texture.fields[0], name_hash(texture.name.decode()))
        self.assertEqual(cache.preview(texture).getpixel((0, 0)), (230, 120, 30, 128))


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.library = sample_library(self.temporary.name)
        self.window = tk.Tk()
        self.app = App(self.window)
        self.app.library = self.library
        self.app.update_sources()
        self.window.update()

    def tearDown(self):
        self.window.destroy()
        self.temporary.cleanup()

    def select(self, keys):
        self.app.rows.selection_set([item for item, key in self.app.current_rows.items() if key in keys])
        self.window.update()

    def test_dropped_image_uploads_to_dropped_row_and_folder_drop_imports_by_name(self):
        from unittest import mock

        image = Path(self.temporary.name) / "art.png"
        Image.new("RGBA", (2, 2), (9, 8, 7, 255)).save(image)
        self.select([NOR])
        row = next(item for item, key in self.app.current_rows.items() if key == NEU)
        self.app.rows.see(row)
        self.window.update()
        y = self.app.rows.bbox(row)[1] + 2 + self.app.rows.winfo_rooty()
        with mock.patch.object(self.window, "winfo_containing", return_value=self.app.rows):
            self.app.handle_drop([str(image)], 0, y)
        self.assertEqual(self.app.selected_keys(), [NEU])
        self.assertEqual(self.library.choices, {NEU: UPLOAD})
        folder = Path(self.temporary.name) / "drop"
        folder.mkdir()
        Image.new("RGBA", (2, 2), (1, 1, 1, 255)).save(folder / "ske_test.png")
        with mock.patch.object(self.app, "run_import") as run_import:
            self.app.handle_drop([str(folder)], 0, 0)
        run_import.assert_called_once_with([str(folder)])

    def test_scan_uses_detected_folder_and_browses_only_when_needed(self):
        import app as appmod
        from unittest import mock

        scanned, browsed = [], []

        def scan(folder):
            scanned.append(str(folder))
            if str(folder) == "bad":
                raise ValueError("not a game")
            return self.library, []

        def wait():
            while self.app.busy:
                self.window.update()
                self.window.after(20)

        with mock.patch.object(appmod.ArtworkLibrary, "scan", side_effect=scan), \
                mock.patch.object(appmod, "remember_game_folder"), \
                mock.patch.object(appmod.messagebox, "showinfo"), \
                mock.patch.object(appmod.filedialog, "askdirectory",
                                  side_effect=lambda **kw: browsed.append(1) or "picked"):
            with mock.patch.object(appmod, "find_game_folder", return_value=Path("found")):
                self.app.open_game()
                wait()
            self.assertEqual((scanned, browsed), (["found"], []))
            with mock.patch.object(appmod, "find_game_folder", return_value=None):
                self.app.open_game()
                wait()
            self.assertEqual((scanned[-1], len(browsed)), ("picked", 1))
            with mock.patch.object(appmod, "find_game_folder", return_value=Path("bad")):
                self.app.open_game()
                wait()
                wait()
            self.assertEqual((scanned[-2:], len(browsed)), (["bad", "picked"], 2))
        root = Path(self.temporary.name) / "w3"
        (root / "content").mkdir(parents=True)
        (root / "bin" / "x64_dx12").mkdir(parents=True)
        from mod import is_game_folder
        self.assertFalse(is_game_folder(root))
        (root / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"")
        self.assertTrue(is_game_folder(root))

    def test_faction_and_source_filters_retain_choices(self):
        self.library.choose([NOR], "First mod")
        self.app.faction.set("Neutral")
        self.app.search.set("test")
        self.assertEqual(set(self.app.current_rows.values()), {NEU})
        self.app.faction.set("All factions")
        self.app.source_filter.set("Second mod")
        self.app.refresh()
        self.assertEqual(set(self.app.current_rows.values()), {NOR, SKE})
        self.assertEqual(self.library.choices, {NOR: "First mod"})
        self.assertEqual(card_faction(r"GAMEPLAY\GUI_NEW\ICONS\GWINT\SKE_TEST.PNG"), "ske")
        self.assertEqual(card_faction("gameplay/gui_new/icons/gwint/cardcover.png"), "other")

    def test_click_chooses_immediately_and_unavailable_batch_source_is_disabled(self):
        self.select([NOR])
        self.assertEqual(self.library.choices, {})
        self.assertEqual(len(self.app.previews), 10)
        self.assertIsNotNone(self.app.preview_images[0])
        self.assertIsNotNone(self.app.preview_images[1])
        self.assertIsNotNone(self.app.preview_images[2])
        self.assertIsNone(self.app.preview_images[4])
        self.app.previews[2].event_generate("<Button-1>", x=20, y=20)
        self.window.update()
        self.assertEqual(self.library.choices, {NOR: "Second mod"})
        self.assertEqual(self.app.captions[2].cget("text"), "second")
        self.assertEqual(self.app.previews[2].cget("highlightbackground"), ACCENT)
        panel = self.app.panels[2]
        self.assertIs(panel.pack_slaves()[0], self.app.captions[2])
        self.assertEqual(int(self.app.captions[2].cget("height")), 2)
        self.assertEqual(self.app.slot_sources[8:], [UPLOAD, NONE])
        self.select([NOR, NEU])
        self.assertFalse(self.app.slot_enabled[2])
        self.app.previews[2].event_generate("<Button-1>", x=20, y=20)
        self.assertEqual(self.library.choices, {NOR: "Second mod"})
        self.app.previews[1].event_generate("<Button-1>", x=20, y=20)
        self.window.update()
        self.assertEqual(self.library.choices, {NOR: "First mod", NEU: "First mod"})
        self.app.previews[0].event_generate("<Button-1>", x=20, y=20)
        self.window.update()
        self.assertEqual(self.library.choices, {NOR: ORIGINAL, NEU: ORIGINAL})

    def test_eight_slot_grid_grows_without_redecoding(self):
        original_preview = self.library.preview
        self.library.preview = lambda key, label: Image.new("RGBA", (309, 444), (230, 120, 30, 255))
        self.select([NOR])
        original = self.app.preview_images[0]
        heights = []
        list_widths = []
        for geometry in ("1100x680", "1920x1080", "2400x900"):
            self.window.geometry(geometry)
            self.window.update()
            heights.append(self.app.images[0].height())
            canvas = self.app.previews[0]
            inner = (canvas.winfo_width() - 12) / (canvas.winfo_height() - 12)
            self.assertAlmostEqual(inner, 308 / 444, delta=0.02)
            list_widths.append(self.app.rows.master.winfo_width())
            self.assertAlmostEqual(self.app.images[0].width() / self.app.images[0].height(), 309 / 444, delta=0.01)
            self.assertIs(self.app.preview_images[0], original)
            big = self.app.chosen_preview
            self.assertAlmostEqual((big.winfo_width() - 12) / (big.winfo_height() - 12), 308 / 444, delta=0.04)
            self.assertGreater(big.winfo_width(), 1.8 * canvas.winfo_width())
            self.assertEqual(
                [(panel.grid_info()["row"], panel.grid_info()["column"]) for panel in self.app.panels],
                [(row, column) for row in range(2) for column in range(5)],
            )
        self.library.preview = original_preview
        self.assertGreater(heights[0], 100)
        self.assertGreater(heights[1], heights[0])
        self.assertGreater(list_widths[2], max(list_widths[:2]) + 200)

    def test_custom_only_path_can_choose_mod_and_reset_to_no_texture(self):
        self.select([SKE])
        self.assertIsNone(self.app.preview_images[0])
        self.assertFalse(self.app.slot_enabled[0])
        self.assertIsNotNone(self.app.preview_images[2])
        self.app.choose_slot(2)
        self.assertEqual(self.library.choices, {SKE: "Second mod"})
        self.app.choose_slot(9)
        self.assertEqual(self.library.choices, {})

    def test_scrolling_reaches_all_sources_with_only_eight_slots(self):
        first = self.library.providers["First mod"]
        mods = {label: mod for label, mod in self.library.providers.items() if label != ORIGINAL}
        for index in range(10):
            mods[f"Extra mod {index}"] = SimpleNamespace(
                root=Path(self.temporary.name) / f"extra{index}",
                artwork=first.artwork, bundled=set(), caches=first.caches,
            )
        self.library = ArtworkLibrary(self.library.game, mods)
        self.app.library = self.library
        self.app.update_sources()
        self.select([NOR])
        seen = set(self.app.slot_sources)
        self.app.previews[0].event_generate("<MouseWheel>", delta=-120)
        self.window.update()
        seen.update(self.app.slot_sources)
        self.app.scroll_sources("moveto", "1")
        self.window.update()
        seen.update(self.app.slot_sources)
        self.assertTrue(set(self.library.providers) <= seen)
        self.assertEqual(len(self.app.previews), 10)
        last_label = list(self.library.providers)[-1]
        index = self.app.slot_sources.index(last_label)
        self.app.choose_slot(index)
        self.window.update()
        self.assertEqual(self.library.choices, {NOR: last_label})
        self.assertEqual(self.app.previews[index].cget("highlightbackground"), ACCENT)
        self.assertEqual(self.app.slot_sources[8:], [UPLOAD, NONE])
        self.app.scroll_sources("moveto", "0")
        self.window.update()
        self.assertEqual(self.app.slot_sources[0], ORIGINAL)
        self.assertEqual(self.library.choices, {NOR: last_label})

    def test_alternate_children_do_not_change_the_main_card(self):
        self.library = with_alternates(self.library, self.temporary.name)
        self.app.library = self.library
        self.app.update_sources()
        self.window.update()
        items = {key: item for item, key in self.app.current_rows.items()}
        self.assertEqual(self.app.rows.parent(items[NOR_ALT]), items[NOR])
        self.assertEqual(self.app.rows.parent(items[NOR_ALT2]), items[NOR])
        self.assertNotEqual(self.app.rows.parent(items[DLC_ALT]), items[NOR])
        self.select([NOR_ALT])
        self.app.choose_slot(self.app.slot_sources.index("Alternate mod"))
        self.assertEqual(self.library.choices, {NOR_ALT: "Alternate mod"})
        self.assertIn("Alternate of", self.app.detail.get())
        self.assertEqual(self.library.chosen_source(NOR), NONE)
        self.app.search.set("alt2")
        self.assertEqual(set(self.app.current_rows.values()), {NOR_ALT2})
        self.assertEqual(self.library.choices, {NOR_ALT: "Alternate mod"})

    def test_edited_column_and_auto_advance_to_next_row(self):
        self.library = with_alternates(self.library, self.temporary.name)
        self.app.library = self.library
        self.app.update_sources()
        self.window.update()
        items = {key: item for item, key in self.app.current_rows.items()}
        self.select([NOR])
        self.app.choose_slot(self.app.slot_sources.index(self.library.defaults[NOR]))
        self.assertEqual(self.library.choices, {})
        edited = {key: self.app.rows.set(item, "edited") for item, key in self.app.current_rows.items()}
        self.assertEqual(edited[NOR], "✓")
        self.assertEqual({key for key, mark in edited.items() if mark}, {NOR})
        self.assertEqual(self.app.selected_keys(), [NOR])

        self.app.auto_next.set(True)
        self.app.choose_slot(self.app.slot_sources.index(NONE))
        self.window.update()
        # The next row is the first alternate child shown under the main card.
        items = {key: item for item, key in self.app.current_rows.items()}
        self.assertEqual(self.app.selected_keys(), [self.app.current_rows[self.app.rows.get_children(items[NOR])        [0]]])

        self.app.select_step("last")
        last = self.app.selected_keys()
        self.app.select_step(1)
        self.assertEqual(self.app.selected_keys(), last)
        self.app.select_step("first")
        first = self.app.selected_keys()
        self.app.select_step(-1)
        self.assertEqual(self.app.selected_keys(), first)
        self.assertNotEqual(first, last)
        items = {key: item for item, key in self.app.current_rows.items()}
        self.app.rows.selection_set(items[NOR])
        self.window.update()
        self.app.select_step(1)
        self.app.select_step(-1)
        self.assertEqual(self.app.selected_keys(), [NOR])
        self.app.reset_selected()
        self.assertNotIn(NOR, self.library.edited)
        self.assertNotIn(NOR, self.library.choices)
        self.assertEqual(self.library.chosen_source(NOR), self.library.defaults[NOR])
    def test_paths_not_used_by_the_selected_xml_are_hidden(self):
        self.library.used_pictures = {"neu_test", "ske_test"}
        self.app.refresh()
        shown = set(self.app.current_rows.values())
        self.assertNotIn(NOR, shown)
        self.assertIn(NEU, shown)
        self.assertIn("unused hidden", self.app.status.get())

    def test_main_xml_selector_rebuilds_card_list_and_warns_on_load_order_mismatch(self):
        from names import card_xml_catalog

        base = Path(self.temporary.name) / "game" / "content"
        mod = Path(self.temporary.name) / "game" / "Mods" / "modXML"
        relative = Path("content") / "gameplay" / "items" / "def_gwint_cards_final.xml"
        xml = '<redxml><card name="one" title="same_title" picture="{}"/></redxml>'
        (base / relative).parent.mkdir(parents=True)
        (base / relative).write_text(xml.format("nor_test"), encoding="utf-8")
        (mod / relative).parent.mkdir(parents=True)
        (mod / relative).write_text(xml.format("neu_test"), encoding="utf-8")
        catalog = card_xml_catalog([base, mod])
        mods = {label: provider for label, provider in self.library.providers.items() if label != ORIGINAL}
        self.library = ArtworkLibrary(self.library.game, mods, xml_catalog=catalog)
        self.app.library = self.library
        self.app.update_sources()
        self.window.update()
        self.assertEqual(set(self.app.current_rows.values()), {NEU})
        original = catalog["options"][0]
        self.app.card_xml.set(original["label"])
        self.app.select_card_xml()
        self.window.update()
        self.assertEqual(set(self.app.current_rows.values()), {NOR})
        self.assertIn("not the XML", self.app.card_xml_note.get())
        project = Path(self.temporary.name) / "xml-choice.gwentproj"
        self.app.library.save_project(project)
        fresh = ArtworkLibrary(self.library.game, mods, xml_catalog=catalog)
        _, _, warnings, main_xml, _ = fresh.read_project(project)
        self.assertEqual(warnings, [])
        self.assertEqual(main_xml, original["id"])
        self.assertTrue(fresh.with_configuration(main_xml).in_use(NOR))

    def test_bundle_and_metadata_writer_round_trip_two_xml_assets(self):
        from names import bundle_files

        files = {
            "dlc/dlcgwentfaithfulartwork/data/gameplay/items/gwent_faithful_artwork.xml":
                b"<redxml><card name='test'/></redxml>",
            "dlc/dlcgwentfaithfulartwork/data/gameplay/items_plus/gwent_faithful_artwork.xml":
                b"<redxml><card name='test'/></redxml>",
        }
        for bundle_version, metadata_version in ((5, 7), (3, 6)):
            data, records, data_start = write_bundle(files, bundle_version)
            bundle_path = Path(self.temporary.name) / f"blob{bundle_version}.bundle"
            bundle_path.write_bytes(data)
            self.assertEqual(dict(bundle_files(bundle_path, lambda _: True)), files)
            metadata = write_metadata("blob0.bundle", files, records, data_start, metadata_version)
            self.assertEqual(metadata[:8], b"\x03VTM" + struct.pack("<I", metadata_version))
            self.assertIn(b"gwent_faithful_artwork.xml\0", metadata)
            self.assertIn(b"blob0.bundle\0", metadata)
            position = 16

            def read_vlq():
                nonlocal position
                first = metadata[position]
                position += 1
                value = first & 0x3f
                shift = 6
                continuing = bool(first & 0x40)
                while continuing:
                    first = metadata[position]
                    position += 1
                    value |= (first & 0x7f) << shift
                    shift += 7
                    continuing = bool(first & 0x80)
                return value

            string_size = read_vlq()
            string_table = metadata[position:position + string_size]
            position += string_size
            file_count = read_vlq()
            self.assertEqual(file_count, 3)
            file_records = [
                struct.unpack_from("<8I", metadata, position + index * 32)
                for index in range(file_count)
            ]
            position += file_count * 32
            entry_count = read_vlq()
            self.assertEqual(entry_count, 3)
            if metadata_version == 7:
                entries = [
                    struct.unpack_from("<Q4I", metadata, position + index * 24)
                    for index in range(entry_count)
                ]
            else:
                entries = [
                    struct.unpack_from("<5I", metadata, position + index * 20)
                    for index in range(entry_count)
                ]
            position += entry_count * (24 if metadata_version == 7 else 20)
            bundle_count = read_vlq()
            self.assertEqual(bundle_count, 2)
            if metadata_version == 7:
                bundle = struct.unpack_from("<Q4I", metadata, position + 24)
                self.assertEqual(bundle[1], data_start)
                self.assertTrue(string_table[bundle[2]:].startswith(b"blob0.bundle\0"))
                self.assertEqual(bundle[3:], (1, 2))
            else:
                bundle = struct.unpack_from("<6I", metadata, position + 24)
                self.assertTrue(string_table[bundle[0]:].startswith(b"blob0.bundle\0"))
                self.assertEqual(bundle[1:3], (1, 2))
                self.assertEqual(bundle[4], data_start)
            self.assertEqual([record[4] for record in file_records[1:]], [1, 2])
            if metadata_version == 7:
                self.assertEqual([entry[3] for entry in entries[1:]], [1, 2])
            else:
                self.assertEqual([entry[0] for entry in entries[1:]], [1, 2])
            self.assertIn(b"blob0.bundle\0", string_table)
            position += bundle_count * 24
            buffer_count = read_vlq()
            position += buffer_count * 4
            directory_count = read_vlq()
            position += directory_count * 8
            file_init_count = read_vlq()
            position += file_init_count * 12
            hash_count = read_vlq()
            position += hash_count * 16
            self.assertEqual(position, len(metadata))

    def test_manual_card_match_borrows_and_renames_donor_texture(self):
        missing = "gameplay/gui_new/icons/gwint/nor_missing.png"
        library = ArtworkLibrary(
            self.library.game,
            {label: mod for label, mod in self.library.providers.items() if label != ORIGINAL},
            used={"nor_missing"},
        )
        matched = library.with_card_match(missing, SKE)
        self.assertIn("Second mod", matched.available[missing])
        matched.choose([missing], "Second mod")
        entry = matched.entry(missing, "Second mod")
        self.assertEqual(entry[1].path, missing)
        self.assertEqual(matched.preview(missing, "Second mod").getpixel((0, 0)), (90, 70, 50, 255))
        project = Path(self.temporary.name) / "matched.gwentproj"
        matched.save_project(project)
        fresh = ArtworkLibrary(
            self.library.game,
            {label: mod for label, mod in self.library.providers.items() if label != ORIGINAL},
            used={"nor_missing"},
        )
        choices, uploads, warnings, main_xml, links = fresh.read_project(project)
        self.assertEqual(warnings, [])
        self.assertEqual(links, {missing: SKE})
        fresh = fresh.with_configuration(main_xml, links)
        fresh.apply_project(choices, uploads)
        self.assertEqual(fresh.entry(missing, "Second mod")[1].path, missing)

    def test_xml_donor_follows_source_xml_then_base_game_xml(self):
        from composer import MOD_NAME, name_roots

        game = Path(self.temporary.name) / "w3"
        for name in (MOD_NAME, "modRedux"):
            (game / "Mods" / name).mkdir(parents=True)
        self.assertEqual([path.name for path in name_roots(game, {})], ["content", "DLC", "modRedux"])
        missing = "gameplay/gui_new/icons/gwint/nor_missing.png"

        def definition(source, is_mod, pictures, text):
            return {
                "id": source, "label": source, "source": source, "asset": "def_gwint_cards_final.xml",
                "is_mod": is_mod, "records": [
                    {"name": picture, "title": f"t_{title}", "title_text": f"{text} {title}", "picture": picture}
                    for title, picture in pictures.items()
                ],
            }

        base = definition("Game original", False, {"mush": "ske_test", "horn": "nor_test"}, "Base")
        own = definition("Mods/first", True, {"mush": "neu_test"}, "First")
        main = definition("Mods/modRedux", True, {"mush": "nor_missing", "horn": "nor_test"}, "Redux")
        catalog = {
            "definitions": [base, own, main], "active": {"def_gwint_cards_final.xml": main},
            "options": [base, main], "default_main": "Mods/modRedux", "main_asset": "def_gwint_cards_final.xml",
            "base_main": base, "patches": [], "strings": {},
        }
        mods = {label: mod for label, mod in self.library.providers.items() if label != ORIGINAL}
        library = ArtworkLibrary(self.library.game, mods, xml_catalog=catalog)
        self.assertEqual(library.xml_donor(missing, "Second mod"), SKE)
        self.assertEqual(library.xml_donor(missing, "First mod"), NEU)
        self.assertEqual(library.xml_donor(NOR, "First mod"), NOR)
        self.assertIsNone(library.xml_donor(NEU, "Second mod"))
        self.assertEqual(library.source_card_name(SKE, "Second mod"), "Base mush")
        self.assertEqual(library.source_card_name(NEU, "First mod"), "First mush")
        self.assertEqual(library.source_paths("Second mod"), [NOR, SKE])
        self.assertEqual(library.available[missing], ("First mod", "Second mod"))
        self.assertEqual(library.preview(missing, "Second mod").getpixel((0, 0)), (90, 70, 50, 255))
        library.choose([missing], "Second mod")
        self.assertEqual(library.entry(missing, "Second mod")[1].path, missing)

    def test_large_preview_shows_chosen_texture(self):
        self.select([NOR])
        self.window.update()
        load_order = self.library.load_order_defaults[NOR]
        self.assertEqual(self.library.chosen_source(NOR), NONE)
        self.assertEqual(self.app.chosen_image, self.library.preview(NOR, load_order))
        self.assertIn(f"Preview: {self.app.display(load_order)}", self.app.chosen_caption.cget("text"))
        self.app.choose_slot(self.app.slot_sources.index("First mod"))
        self.select([NOR])
        self.window.update()
        self.assertEqual(self.app.chosen_image.getpixel((0, 0)), (230, 120, 30, 128))
        self.assertIn(self.app.display("First mod"), self.app.chosen_caption.cget("text"))
        self.assertIsNotNone(self.app.chosen_photo)

    def test_uploaded_slot_click_selects_and_menu_uploads_again(self):
        from unittest import mock

        image = Path(self.temporary.name) / "art.png"
        Image.new("RGBA", (2, 2), (9, 8, 7, 255)).save(image)
        self.select([NOR])
        upload = self.app.slot_sources.index(UPLOAD)
        with mock.patch("app.filedialog.askopenfilename", return_value=str(image)) as ask:
            self.app.choose_slot(upload)
            self.app.choose_slot(self.app.slot_sources.index("First mod"))
            self.app.choose_slot(upload)
            self.assertEqual(ask.call_count, 1)
            self.assertEqual(self.library.choices, {NOR: UPLOAD})
            dict(self.app.slot_actions(upload))["Upload new image..."]()
            self.assertEqual(ask.call_count, 2)

    def test_slot_menu_picks_card_artwork_from_a_source_grid(self):
        self.app.auto_next.set(False)
        self.select([NEU])
        second = self.app.slot_sources.index("Second mod")
        self.assertFalse(self.app.slot_enabled[second])
        dict(self.app.slot_actions(second))["Choose card from second..."]()
        dialog = next(child for child in self.window.winfo_children() if isinstance(child, tk.Toplevel))
        canvas = next(widget for widget in dialog.winfo_children()[-1].winfo_children() if isinstance(widget, tk.Canvas))
        for _ in range(50):
            self.window.update()
            self.window.after(20)
        cell = canvas.winfo_width() / 10
        canvas.event_generate("<Button-1>", x=int(cell * 1.5), y=20)
        self.window.update()
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(self.app.library.manual_links, {NEU: SKE})
        self.assertEqual(self.app.library.choices, {NEU: "Second mod"})
        self.assertEqual(self.app.library.preview(NEU, "Second mod").getpixel((0, 0)), (90, 70, 50, 255))

    def test_right_click_menus_open_and_save_slot_chosen_and_row_images(self):
        from unittest import mock

        self.select([NOR])
        first = self.app.slot_sources.index("First mod")
        none = self.app.slot_sources.index(NONE)
        self.assertEqual([command for _, command in self.app.slot_actions(none)], [None, None])
        with mock.patch("app.os.startfile") as startfile:
            dict(self.app.slot_actions(first))["Open image"]()
        with Image.open(startfile.call_args[0][0]) as image:
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (230, 120, 30, 128))
        target = Path(self.temporary.name) / "saved.png"
        with mock.patch("app.filedialog.asksaveasfilename", return_value=str(target)):
            dict(self.app.slot_actions(first))["Save image as..."]()
        with Image.open(target) as image:
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (230, 120, 30, 128))
        self.assertIsNotNone(dict(self.app.chosen_actions())["Open image"])
        item = next(item for item, key in self.app.current_rows.items() if key == NEU)
        self.app.rows.see(item)
        self.window.update()
        event = SimpleNamespace(y=self.app.rows.bbox(item)[1] + 2)
        actions = dict(self.app.row_actions(event))
        self.assertEqual(self.app.selected_keys(), [NEU])
        self.assertIsNone(actions["Open chosen image"])
        self.app.choose_slot(first)
        self.select([NEU])
        with mock.patch("app.os.startfile") as startfile:
            dict(self.app.row_actions(event))["Open chosen image"]()
        with Image.open(startfile.call_args[0][0]) as image:
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (10, 60, 200, 255))

    def test_arrow_keys_move_between_cards_and_cycle_chosen_texture(self):
        self.select([NOR])
        self.app.rows.focus_force()
        self.window.update()
        for key in ("Right", "Right", "Right"):
            self.app.rows.event_generate(f"<{key}>")
            self.window.update()
        self.assertEqual(self.library.chosen_source(NOR), "Second mod")
        self.app.rows.event_generate("<Left>")
        self.window.update()
        self.assertEqual(self.library.chosen_source(NOR), "First mod")
        self.app.rows.event_generate("<Down>")
        self.window.update()
        moved = self.app.selected_keys()
        self.assertNotEqual(moved, [NOR])
        self.app.rows.event_generate("<Up>")
        self.window.update()
        self.assertEqual(self.app.selected_keys(), [NOR])
        self.assertEqual(self.library.chosen_source(NOR), "First mod")
        self.app.rows.event_generate("<Left>")
        self.window.update()
        self.assertEqual(self.library.chosen_source(NOR), ORIGINAL)
        self.app.rows.event_generate("<Left>")
        self.window.update()
        self.assertEqual(self.library.chosen_source(NOR), NONE)

    def test_remove_source_button_uses_show_cards_from_choice(self):
        from unittest import mock

        self.window.update()
        self.assertIn("disabled", self.app.remove_button.state())
        self.app.source_filter.set("First mod")
        self.app.refresh()
        self.assertNotIn("disabled", self.app.remove_button.state())
        with mock.patch("app.messagebox.askyesno", return_value=True):
            self.app.remove_source()
        self.assertNotIn("First mod", self.app.library.providers)
        self.assertEqual(self.app.source_filter.get(), "All sources")
        self.assertEqual(self.app.add_button.master, self.app.source_picker.master)

    def test_no_texture_slot_works_if_original_preview_fails(self):
        self.library.choose([NOR], "First mod")
        preview = self.library.preview

        def failed_original(key, label):
            if label == ORIGINAL:
                raise ValueError("Original cache unavailable")
            return preview(key, label)

        self.library.preview = failed_original
        self.select([NOR])
        self.assertIn("Original cache unavailable", self.app.detail.get())
        self.assertFalse(self.app.slot_enabled[0])
        self.app.choose_slot(9)
        self.assertEqual(self.library.chosen_source(NOR), NONE)


if __name__ == "__main__":
    unittest.main()
