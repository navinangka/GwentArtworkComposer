Gwent Artwork Composer for Windows
Version 0.2 beta

Build a new artwork-only mod by choosing a texture source for each Gwent
artwork path. The EXE runs without a separate Python installation.
Source mods and game files are never modified.

1. Open GwentArtworkComposer.exe and click Scan game + mods.
   The game folder is found automatically (last used folder, Steam
   libraries, GOG/Epic installs, common paths). A folder picker appears
   only if it is not found or the found folder cannot be scanned. The last
   successful folder is remembered in %APPDATA%\GwentArtworkComposer.
   The app scans original content, official DLC, and installed Mods/custom
   DLC. It lists mods containing Gwent artwork, not unrelated gameplay mods.
   Vortex deployment records resolve artwork to its staging package.
   Any scan failures are reported instead of silently ignored.
2. Optionally Add mod (next to Show cards from) to include an additional mod
   folder or staging package that is not installed. Remove source removes
   the mod picked in Show cards from (not Game original) from this session
   only - no files are deleted; cards chosen from it go back to No
   texture. Scan again to bring it back.
3. Choose the Main card XML. By default this is the XML selected by the
   current load order. It controls the main card names and picture paths;
   other card-definition XML files continue to follow the game's load order.
   Choosing a different XML shows a warning because its paths may not be the
   ones the game loads. Only paths used by the selected main XML or active
   companion card XMLs are listed; there is no Show unused toggle.
   Filter by Faction, Search, or Show cards from. This source filter affects
   the list only: the image gallery always includes all scanned sources.
   The list shows each card's real English name (read from the game, DLC
   and installed mods' card definitions and en.w3strings, following load
   order), plus its File and Chosen source. Search matches the card name or
   the asset path (e.g. "geralt" or "neu_geralt"). Cards with no defined
   name (e.g. cardcover) show the file name. Artwork itself is still
   matched by asset path, not by name.
   Card XML from mods: the game uses each def_gwint*.xml from the highest
   priority mod only (no merging). When that XML points a card to a new
   picture (e.g. Gwent Redux: Triss neu_triss -> nor_triss), the new path's
   gallery also shows textures other mods made for the old path, and the
   export writes your pick under the path the game really loads. The
   details line says "Card moved from ... by mod XML".
   Paths not used by the selected card XML are hidden. The list still shows
   the exact texture paths used for matching and export.
4. Select a card to see textures in ten fixed slots (5 columns x 2 rows,
   source name above each image, always two caption lines). Slots 1-8 show
   Game original then mods in load order; slot 9 is Uploaded image and
   slot 10 is always No texture.
   Uploaded image: click slot 9 and choose a PNG/JPG/WEBP/BMP/TGA/DDS file.
   It is center-cropped and resized to that card path's own texture size
   (e.g. 308x444) and exported as an uncompressed RGBA texture. With
   several cards selected, each gets its own sized copy. Once uploaded,
   clicking slot 9 just selects the upload again; to replace it,
   right-click slot 9 and choose Upload new image...
   Click the desired image to choose it immediately. A blue border marks
   the current choice (gold: chosen for only some selected paths).
   Every card starts as No texture, so only the cards you pick are
   exported. The details under the images show "Load order:", the source
   the game itself would use (Documents\The Witcher 3\mods.settings, lower
   Priority wins; disabled mods are skipped; mods not listed come after
   listed ones alphabetically; custom DLC folders after Mods); the slots
   use the same order.
   With more than eight sources, use the mouse wheel over a slot to scroll
   through the remaining sources.
   Ctrl/Shift multi-selection applies a clicked source only to the selected
   paths. Sources missing any selected path are not clickable.
   Filtering keeps choices.
   The Edited column shows a check mark on every card you picked an image
   for (even if it is No texture); it is saved in
   the project file. Under the images: ⏮ first card, ◀ previous, ▶ next,
   ⏭ last card in the list; Reset to default returns the selected cards
   to No texture (removes their upload and Edited mark), and
   "Go to next card after choosing" jumps to the next row after
   each click, for fast card-by-card work.
   Right-click menu:
   - Same card at another path: when a mod stores a card's art under a
     different file name (e.g. Faithful's faithful_geralt for
     neu_geralt), the card XMLs (matched by card title) are used to show
     that art directly in the mod's slot; click it as usual. The details
     line "Same card by XML at another path" names the file used.
   - on a source slot (one card selected): Choose card from <mod>... opens
     a grid of every card image in that mod (10 per row, card name and
     file name below; type in the box to filter). The card that the XMLs
     say is the same card (the mod's own XML, Faithful's patch XML, else
     the base game XML, matched by card title) is highlighted in gold and
     scrolled into view; the current match has a blue border. Click any
     card to use its artwork for the selected card (it is exported under
     the selected card's path). Remove card match clears the mapping.
     Also Open image / Save image as... for that source;
   - on Uploaded image: Upload new image... replaces the upload;
   - on the large preview: the image it shows;
   - on a list row: Open chosen image / Save chosen image as... (several
     selected rows: saves them all into a folder you pick).
   Open image opens a PNG in the Windows default image app.
   Keyboard: Up / Down arrow = previous / next card; Left / Right arrow =
   choose the previous / next texture source for the selected card(s), in
   slot order (sources, Upload when the card has one, No texture), wrapping
   around. Sources missing a selected card are skipped; the gallery scrolls
   to show the chosen source. Arrows are ignored while typing in a box.
   Image slots keep the card's aspect ratio (no empty side borders); when
   the window grows, the extra width goes to the card list on the left.
   The large card on the right (two rows tall) previews the texture chosen
   for the selected card ("Chosen: source"); it updates on every click.
   If the card is still "No texture", it previews the highest-priority
   mod's texture by load order (or the game original), labeled
   "Preview: source (load order)".
   Alternate filenames ending in _alt, _alt1, _alt2, etc. appear as children
   under the matching main card. Each alternate has its
   own choice and is exported at its original path: selecting a main card
   does not select its children, and selecting an alternate does not replace
   the main image or its siblings. DLC paths are kept separate; a _dlc suffix
   alone is not treated as an _alt variant. Missing main paths are shown as
   group headings only and are not exported.
5. Export mod ZIP. It contains:
   Mods/mod0000GwentArtworkComposer/content/texture.cache
   Mods/mod0000GwentArtworkComposer/content/metadata.store
   and, when Faithful Gwent Artwork DLC XML is detected:
   Mods/mod0000GwentArtworkComposer/content/blob0.bundle
   Every path not set to No texture is exported. Paths set to Game original
   include the original texture, so other mods cannot override them.
   The compatibility bundle rewrites Faithful's card name/picture patches
   against the selected main XML. Only confidently matched cards are kept;
   ambiguous or conflicting entries are omitted. It changes no card power,
   faction, abilities, or other gameplay rules. Give this mod the highest
   artwork/XML priority and verify the result in game; overriding a DLC
   bundle asset has not been validated in every load-order configuration.
   Other scripts, strings, gameplay assets and unrelated textures are not
   included. Cooked texture payloads and mip data are copied
   unchanged: previews are NOT used to regenerate exported textures.
   Textures from older (v6, e.g. Gwent Redux) caches are written with the
   next-gen (v7) record flag, so they display correctly in game.
   PNG export (optional, does not affect the ZIP):
   Export all PNG saves every path not set to No texture, using each
   card's chosen source, into a folder keeping asset paths (e.g.
   gameplay\gui_new\icons\gwint\neu_geralt.png) so DLC/alternate files
   never collide. To save a single card's image, right-click the card
   (see step 4).
   Import images... and Export all PNG... sit together in the toolbar.
6. Install the ZIP through Vortex and give the new mod highest artwork
   priority above other mods overriding the same paths, then Deploy.

Projects (save your work)
   Save project writes a .gwentproj file containing the main card XML,
   manual cross-card matches, every choice that you made, and all uploaded
   images (PNG). Open project
   (or drop a .gwentproj onto the window) restores them after a new scan,
   even on another PC: sources are matched by name, then by mod folder name.
   Entries whose path or source mod is no longer installed are skipped and
   listed. Opening a project replaces the current choices and uploads.
   Scanning again clears choices and uploads, so save first.

Import images (bulk upload)
   Import images picks a folder. Images (PNG/JPG/WEBP/BMP/TGA/DDS) are
   matched to cards by asset path first (the folder layout written by
   Export all PNG, e.g. gameplay\gui_new\icons\gwint\neu_geralt.png), then
   by file name alone (neu_geralt.png, case-insensitive). A file-name-only
   match applies to every path with that name unless another file matched
   that path exactly. Each image is center-cropped to its card's size and
   chosen as Uploaded image. Unmatched files are listed.
   Workflow: Export all PNG -> edit images -> Import images -> Export ZIP.

Drag & drop (from Explorer)
   - One image dropped on a card row or an image slot: uploads it to the
     selected cards (dropping on an unselected row selects that row first).
   - A folder, several images, or an image dropped elsewhere: the same
     matching as Import images.
   - A .gwentproj file: opens the project.

IMPORTANT: Only paths set to No texture are left out; any other enabled
mod overriding those paths still shows its picture. The exported mod
must have the highest priority for its choices to win. The composer does
not edit or prune existing mods and never changes mods.settings.

Supported selection/export: cooked Gwent textures in HCXT v6/v7 caches
under gui_new/icons/gwint. Bundle-only or loose image entries without
a cached texture are listed but explicitly not selectable. Unknown
filename prefixes/card backs are in the Other faction.
The ZIP is validated structurally; final priority and rendering must
be checked in game after installation.

Updating / building from source
Change source/version.py, then run build.bat from the project root.
It requires Python 3.12+ and the Python launcher. On the first run, it
creates .venv and installs Pillow and PyInstaller (internet required).
The build uses that version for the title and EXE metadata and writes
dist\GwentArtworkComposer.exe, replacing any existing file at that path.
Users need only the EXE.
Run tests with: .venv\Scripts\python.exe -m unittest discover -s source -p test_*.py

Format reference
https://github.com/Systemcluster/w3edit/blob/main/w3edit-formats.md
