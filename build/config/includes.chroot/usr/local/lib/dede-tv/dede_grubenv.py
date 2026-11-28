"""GRUB environment block (grubenv) on the dede-boot partition.

GRUB's load_env/save_env read and rewrite this fixed 1024-byte file in place,
so it is always written whole and padded with '#'.
"""
import os
import re
from pathlib import Path

SIZE = 1024
HEADER = "# GRUB Environment Block\n"
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")
VALUE = re.compile(r"[A-Za-z0-9._-]{0,64}")


def read(path):
    try:
        text = Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return {}
    values = {}
    for line in text.splitlines()[1:]:
        name, sep, value = line.partition("=")
        if sep and NAME.fullmatch(name) and VALUE.fullmatch(value):
            values[name] = value
    return values


def write(path, values):
    body = HEADER + "".join(f"{k}={v}\n" for k, v in sorted(values.items()) if v != "")
    for name, value in values.items():
        if not NAME.fullmatch(name) or not VALUE.fullmatch(value):
            raise ValueError(f"bad grubenv entry {name!r}")
    if len(body) > SIZE:
        raise ValueError("grubenv too large")
    data = (body + "#" * (SIZE - len(body))).encode("ascii")
    path = Path(path)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    with open(temp, "wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def update(path, **changes):
    values = read(path)
    values.update(changes)
    write(path, {k: v for k, v in values.items() if v != ""})
