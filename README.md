# Gwent Artwork Composer

A Windows tool for combining Gwent card artwork from The Witcher 3 and installed
mods. It can also use images supplied by the user. For usage instructions, see
[dist/README.txt](dist/README.txt).

## Manual build from source

These steps let reviewers build the executable without using the included
binary or `build.bat`.

### Requirements

- Windows 10 or 11.
- [Python 3.12 or newer](https://www.python.org/downloads/windows/), with pip,
  Tkinter, and the Python launcher (`py`). The standard Windows Python installer
  can provide all three.
- Internet access to install the Python packages Pillow and PyInstaller.

The Witcher 3 is not needed to build the executable. It is needed to test
scanning actual game files and mods.

### Build

Clone this repository or download and extract its source ZIP. Open PowerShell in
the repository folder, the folder containing `source` and `build.bat`.

1. Check that the selected Python version is 3.12 or newer:

   ```powershell
   py -3 --version
   ```

2. Create an isolated Python environment and install the build dependencies:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install Pillow PyInstaller
   ```

3. Build the Windows executable:

   ```powershell
   .\.venv\Scripts\python.exe source\build.py
   ```

The result is `dist\GwentArtworkComposer.exe`. The build replaces any existing
file at that path. The executable runs without a separate Python installation.

To run the application directly from source instead:

```powershell
.\.venv\Scripts\python.exe source\app.py
```

### Tests

From the repository folder, using the same environment:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s source -p 'test_*.py'
```

The test suite includes Tkinter interface tests and needs a graphical desktop
session.

### Build details

[source/build.py](source/build.py) invokes PyInstaller with `--onefile`,
`--windowed`, and `--clean`. It uses
[source/version.py](source/version.py) and
[source/version_info.txt](source/version_info.txt) for the executable's version
metadata. `--clean` also clears PyInstaller's build cache.

One successful build used 64-bit Python 3.14.4, Pillow 13.0.0, and PyInstaller
6.22.3 on Windows. The commands above install unpinned package versions; changes
in Python, dependencies, or the build environment can change the executable's
size and hash. Builds are not guaranteed to be byte-for-byte identical.
