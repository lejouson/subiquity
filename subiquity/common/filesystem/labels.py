# Copyright 2021 Canonical, Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import functools

from subiquity.common.filesystem import boot, gaps
from subiquity.common.types import storage as types
from subiquity.models.filesystem import (
    ZFS,
    Disk,
    DM_Crypt,
    LVM_LogicalVolume,
    LVM_VolGroup,
    Partition,
    Raid,
    ZPool,
)


def _annotations_generic(device):
    preserve = getattr(device, "preserve", None)
    if preserve is None:
        return []
    elif preserve:
        # A pre-existing device such as a partition or RAID
        return [_("existing")]
    else:
        # A newly created device such as a partition or RAID
        return [_("new")]


@functools.singledispatch
def annotations(device):
    """annotations that should be displayed alongside `device`

    The main example is whether this is a new or pre-existing device but
    partitions, particularly bootloader partitions, can get a richer set
    of annotations.
    """
    return _annotations_generic(device)


@annotations.register(Disk)
@annotations.register(gaps.Gap)
def _annotations_none(disk):
    return []


@annotations.register(Partition)
def _annotations_partition(partition):
    r = _annotations_generic(partition)
    if partition.flag == "prep":
        r.append(_("PReP"))
        if partition.preserve:
            if partition.grub_device:
                # boot loader partition
                r.append(_("configured"))
            else:
                # boot loader partition
                r.append(_("unconfigured"))
    elif boot.is_esp(partition):
        if partition.fs() and partition.fs().mount():
            r.append(_("primary ESP"))
        elif partition.grub_device:
            r.append(_("backup ESP"))
        else:
            r.append(_("unused ESP"))
    elif partition.flag == "bios_grub":
        if partition.preserve:
            if partition.device.grub_device:
                r.append(_("configured"))
            else:
                r.append(_("unconfigured"))
        r.append(_("BIOS grub spacer"))
    elif partition.flag == "extended":
        # extended partition
        r.append(_("extended"))

    if partition.is_logical:
        # logical partition
        r.append(_("logical"))

    return r


@annotations.register(LVM_VolGroup)
def _annotations_vg(vg):
    r = _annotations_generic(vg)
    member = next(iter(vg.devices))
    if member.type == "dm_crypt":
        # Flag for a LVM volume group
        r.append(_("encrypted"))
    return r


@functools.singledispatch
def desc(device):
    """A description of the class of thing device is part of.

    E.g. "partition of local disk" or "LVM volume group".
    """
    raise NotImplementedError(repr(device))


@desc.register(Disk)
def _desc_disk(disk):
    if disk.multipath:
        return _("multipath device")
    if disk.on_remote_storage():
        if disk.nvme_controller is not None and disk.nvme_controller.transport == "tcp":
            return _("NVMe/TCP drive")
        # At time of writing, only NVMe/TCP drives will report as "remote".
        # Let's set a default label for potential transports that we may
        # support in the future.
        return _("remote drive")
    return _("local disk")


@desc.register(Partition)
def _desc_partition(partition):
    return _("partition of {device}").format(device=desc(partition.device))


@desc.register(Raid)
def _desc_raid(raid):
    level = raid.raidlevel
    if level.lower().startswith("raid"):
        level = level[4:]
    if raid.container:
        raid_type = raid.container.metadata
    elif raid.metadata == "imsm":
        raid_type = "imsm"  # maybe VROC?
    else:
        raid_type = _("software")
    return _("{type} RAID {level}").format(type=raid_type, level=level)


@desc.register(LVM_VolGroup)
def _desc_vg(vg):
    return _("LVM volume group")


@desc.register(LVM_LogicalVolume)
def _desc_lv(lv):
    return _("LVM logical volume")


@desc.register(gaps.Gap)
def _desc_gap(gap):
    # This is only used in text "cannot add partition {desc}"... bit hackish.
    return _("to gap")


@desc.register(ZPool)
def _desc_zpool(zpool):
    return _("zpool")


@functools.singledispatch
def label(device, *, short=False):
    """A label that identifies `device`

    E.g. the serial number for a disk, or the name of a logical volume
    or a string like "partition $n of $disk_serial". Passing short=True
    returns just "partition $n" for a partition, for when what it is a
    partition of is obvious.
    """
    raise NotImplementedError(repr(device))


@label.register(Disk)
def _label_disk(disk, *, short=False):
    if disk.multipath and disk.wwn:
        return disk.wwn
    if disk.serial:
        return disk.serial
    return disk.path


@label.register(Raid)
@label.register(LVM_VolGroup)
@label.register(LVM_LogicalVolume)
def _label_just_name(device, *, short=False):
    return device.name


@label.register(Partition)
def _label_partition(partition, *, short=False):
    if partition.is_logical:
        p = "  "
    else:
        p = ""
    if short:
        return p + _("partition {number}").format(number=partition.number)
    else:
        return p + _("partition {number} of {device}").format(
            number=partition.number, device=label(partition.device)
        )


@label.register(gaps.Gap)
def _label_gap(gap, *, short=False):
    r = _("free space")
    if gap.in_extended:
        r = "  " + r
    return r


def _usage_labels_generic(device, *, exclude_final_unused=False):
    cd = device.constructed_device()
    if cd is not None:
        return [
            _("{component_name} of {desc} {name}").format(
                component_name=cd.component_name, desc=desc(cd), name=cd.name
            ),
        ]
    fs = device.fs()
    if fs is not None:
        if fs.preserve:
            format_desc = _("already formatted as {fstype}")
        elif device.original_fstype() is not None:
            format_desc = _("to be reformatted as {fstype}")
        else:
            format_desc = _("to be formatted as {fstype}")
        r = [format_desc.format(fstype=fs.fstype)]
        if device._m.is_mounted_filesystem(fs.fstype):
            m = fs.mount()
            if m:
                # A filesytem
                r.append(_("mounted at {path}").format(path=m.path))
            elif device._is_in_use:
                r.append(_("in use"))
            elif not boot.is_esp(device):
                # A filesytem
                r.append(_("not mounted"))
        elif fs.preserve:
            if fs.mount() is None:
                # A filesytem that cannot be mounted (i.e. swap)
                # is used or unused
                r.append(_("unused"))
            else:
                # A filesytem that cannot be mounted (i.e. swap)
                # is used or unused
                r.append(_("used"))
        return r
    else:
        if exclude_final_unused:
            return []
        else:
            return [_("unused")]


@functools.singledispatch
def usage_labels(device):
    """A list of strings that describe how `device` is used.

    E.g. ["component of software RAID 5 md0"] or ["to be reformatted as
    xfs", "not mounted"].
    """
    return _usage_labels_generic(device)


@usage_labels.register(Partition)
def _usage_labels_partition(partition):
    if partition.flag == "prep" or partition.flag == "bios_grub":
        return []
    return _usage_labels_generic(partition)


@usage_labels.register(Disk)
def _usage_labels_disk(disk):
    usages = _usage_labels_generic(disk, exclude_final_unused=True)

    if disk.ptable == "unsupported":
        usages.append(_("unsupported partition table"))

    return usages


@usage_labels.register(Raid)
def _usage_labels_raid(raid):
    if raid.metadata == "imsm" and raid._subvolumes:
        return [
            _("container for {devices}").format(
                devices=", ".join([label(v) for v in raid._subvolumes])
            )
        ]
    return _usage_labels_generic(raid)


@usage_labels.register(LVM_VolGroup)
def _usage_labels_vg(vg):
    return []


@usage_labels.register(gaps.Gap)
def _usage_labels_gap(gap):
    return []


@functools.singledispatch
def for_client(device, *, min_size=0):
    """Return an API-friendly description of `device`"""
    raise NotImplementedError(repr(device))


@for_client.register(Disk)
@for_client.register(Raid)
def _for_client_disk(disk, *, min_size=0):
    return types.Disk(
        id=disk.id,
        label=label(disk),
        path=getattr(disk, "path", None),
        type=desc(disk),
        size=disk.size,
        ptable=disk.ptable,
        preserve=disk.preserve,
        usage_labels=usage_labels(disk),
        partitions=[for_client(p) for p in gaps.parts_and_gaps(disk)],
        boot_device=boot.is_boot_device(disk),
        can_be_boot_device=boot.can_be_boot_device(disk),
        ok_for_guided=disk.size >= min_size and not disk.on_remote_storage(),
        model=getattr(disk, "model", None),
        vendor=getattr(disk, "vendor", None),
        has_in_use_partition=disk._has_in_use_partition,
        requires_reformat=disk.ptable == "unsupported",
    )


def _resolve_constructed_object(partition: Partition) -> tuple[str, str, bool]:
    """provide answers for labels for VERY simple cases of constructed devices.
    This is not really the correct way to represent arbitrarily complex trees
    of block devices but sufficient for what guided does."""
    cd = partition.constructed_device()
    if isinstance(cd, ZPool):
        encrypted = False
        if cd.encryption_style == "luks_keystore":
            encrypted = True
        return cd.mountpoint, cd.fstype, encrypted
    elif isinstance(cd, LVM_VolGroup):
        lvs = cd.partitions()
        # guided produces one of these.  with a non-one amount we don't
        # know what to show for the effective values.
        if len(lvs) == 1:
            encrypted = False
            maybe_dmc = partition.constructed_device(skip_dm_crypt=False)
            if isinstance(maybe_dmc, DM_Crypt):
                encrypted = True
            return lvs[0].mount, lvs[0].format, encrypted

    cd = partition.constructed_device(skip_dm_crypt=False)
    if isinstance(cd, DM_Crypt):
        return cd.mount, cd.format, True

    return partition.mount, partition.format, False


@for_client.register(Partition)
def _for_client_partition(partition, *, min_size=0):
    e_mount, e_format, e_encrypted = _resolve_constructed_object(partition)
    return types.Partition(
        size=partition.size,
        number=partition.number,
        wipe=partition.wipe,
        preserve=partition.preserve,
        grub_device=partition.grub_device,
        boot=partition.boot,
        annotations=annotations(partition) + usage_labels(partition),
        os=partition.os,
        offset=partition.offset,
        resize=partition.resize,
        path=partition._path(),
        name=partition.partition_name,
        estimated_min_size=partition.estimated_min_size,
        mount=partition.mount,
        format=partition.format,
        is_in_use=partition._is_in_use,
        effective_mount=e_mount,
        effective_format=e_format,
        effectively_encrypted=e_encrypted,
    )


@for_client.register(gaps.Gap)
def _for_client_gap(gap, *, min_size=0):
    return types.Gap(offset=gap.offset, size=gap.size, usable=gap.usable)


@for_client.register(ZPool)
def _for_client_zpool(zpool, *, min_size=0):
    return types.ZPool(
        pool=zpool.pool,
        mountpoint=zpool.mountpoint,
        zfses=[for_client(zfs) for zfs in zpool.zfses],
        pool_properties=zpool.pool_properties,
        fs_properties=zpool.fs_properties,
        default_features=zpool.default_features,
    )


@for_client.register(ZFS)
def _for_client_zfs(zfs, *, min_size=0):
    return types.ZFS(volume=zfs.volume, properties=zfs.properties)
