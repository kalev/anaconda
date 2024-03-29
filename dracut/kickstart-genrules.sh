#!/bin/sh
# generate udev rules for fetching kickstarts.

case "${kickstart%%:*}" in
    http|https|ftp|nfs)
        # handled by fetch-kickstart-net in the online hook
        wait_for_kickstart
    ;;
    cdrom|hd|bd) # cdrom:<dev>, hd:<dev>:<path>, bd:<dev>:<path>
        splitsep ":" "$kickstart" kstype ksdev kspath
        ksdev=$(disk_to_dev_path $ksdev)
        if [ "$kstype" = "bd" ]; then # TODO FIXME: no biospart support yet
            warn "inst.ks='$kickstart'"
            warn "can't get kickstart: biospart isn't supported yet"
            ksdev=""
        else
            when_diskdev_appears $ksdev \
                fetch-kickstart-disk \$env{DEVNAME} $kspath
            wait_for_kickstart
        fi
    ;;
esac
