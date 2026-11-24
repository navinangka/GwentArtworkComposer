"""Build the Windows executable using the version in version.py."""

from pathlib import Path
import re
import tempfile

import PyInstaller.__main__

from version import __version__


def build():
    source = Path(__file__).resolve().parent
    parts = tuple(int(part) for part in re.match(r"[\d.]*", __version__)[0].strip(".").split(".") if part)
    if not 2 <= len(parts) <= 4 or any(not 0 <= part <= 65535 for part in parts):
        raise ValueError("Version must contain two to four numbers between 0 and 65535.")
    numbers = parts + (0,) * (4 - len(parts))
    metadata = (source / "version_info.txt").read_text(encoding="utf-8")
    metadata = re.sub(r"(filevers|prodvers)=\([^)]*\)", lambda match: f"{match[1]}={numbers}", metadata)
    for name, value in (("FileVersion", ".".join(map(str, numbers))), ("ProductVersion", __version__)):
        metadata = re.sub(rf"StringStruct\('{name}', '[^']*'\)", f"StringStruct('{name}', '{value}')", metadata)
    with tempfile.TemporaryDirectory(prefix="gwent-build-") as directory:
        temporary = Path(directory)
        resource = temporary / "version_info.txt"
        resource.write_text(metadata, encoding="utf-8")
        PyInstaller.__main__.run([
            "--noconfirm", "--clean", "--onefile", "--windowed",
            "--name", "GwentArtworkComposer",
            "--version-file", str(resource),
            "--distpath", str(source.parent / "dist"),
            "--workpath", str(temporary / "build"),
            "--specpath", str(temporary),
            "--paths", str(source),
            str(source / "app.py"),
        ])


if __name__ == "__main__":
    build()
