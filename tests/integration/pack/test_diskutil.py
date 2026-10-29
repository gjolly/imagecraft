# Copyright 2026 Canonical Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranties of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Integration tests for filesystem population."""

import os
import shutil
import subprocess
from uuid import uuid4

import pytest
from imagecraft.models import FileSystem
from imagecraft.pack.diskutil import format_device
from imagecraft.utils.mount import VirtualOffsetDevice


@pytest.mark.parametrize("virtual_device", [False, True])
def test_populate_btrfs(tmp_path, virtual_device):
    """Populate fixed-size files and FUSE slices without losing Unix metadata."""
    for tool in ("mkfs.btrfs", "btrfs", *(["fusefile"] if virtual_device else [])):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not installed")
    content = tmp_path / "content"
    content.mkdir()
    source = content / "hello"
    source.write_text("hello btrfs\n")
    source.chmod(0o751)
    os.setxattr(source, "user.imagecraft", b"preserved")
    (content / "hardlink").hardlink_to(source)
    (content / "symlink").symlink_to("hello")
    size = 512 << 20
    offset = 1 << 20 if virtual_device else 0
    image = tmp_path / "disk.img"
    with image.open("wb") as stream:
        stream.truncate(size + 2 * offset)
        if virtual_device:
            stream.write(b"prefix")
            stream.seek(size + offset)
            stream.write(b"suffix")
    fs_uuid = str(uuid4())

    def populate(device):
        format_device(
            device_path=device,
            fstype=FileSystem.BTRFS,
            content_dir=content,
            label="writable",
            uuid=fs_uuid,
        )
        subprocess.run(["btrfs", "check", "--readonly", str(device)], check=True)
        tree = subprocess.run(
            ["btrfs", "inspect-internal", "dump-tree", "-t", "5", str(device)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert fs_uuid in tree
        assert "mode 100751 links 2" in tree
        assert f"uid {os.getuid()} gid {os.getgid()}" in tree
        assert "name: user.imagecraft" in tree
        assert "data preserved" in tree
        assert "name: hello" in tree
        assert "name: hardlink" in tree
        assert "type SYMLINK" in tree

    if virtual_device:
        with VirtualOffsetDevice(image, offset, size) as device:
            populate(device)
        with image.open("rb") as stream:
            assert stream.read(6) == b"prefix"
            stream.seek(size + offset)
            assert stream.read(6) == b"suffix"
    else:
        populate(image)
    assert image.stat().st_size == size + 2 * offset


def test_btrfs_partition_too_small(tmp_path):
    """An oversized rootfs must not grow the image past its partition."""
    for tool in ("mkfs.btrfs", "fusefile"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not installed")
    content = tmp_path / "content"
    content.mkdir()
    with (content / "large").open("wb") as stream:
        stream.truncate(600 << 20)
    image = tmp_path / "disk.img"
    size = 256 << 20
    with image.open("wb") as stream:
        stream.truncate(size)
    with VirtualOffsetDevice(image, 0, size) as device:
        with pytest.raises(subprocess.CalledProcessError):
            format_device(
                device_path=device,
                fstype=FileSystem.BTRFS,
                content_dir=content,
            )
    assert image.stat().st_size == size
