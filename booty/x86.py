import os
import string

from booty import BootyNoKernelWarning
from util import getDiskPart
from bootloaderInfo import *
import checkbootloader
import iutil
import rhpl

class x86BootloaderInfo(efiBootloaderInfo):
    def setPassword(self, val, isCrypted = 1):
        if not val:
            self.password = val
            self.pure = val
            return
        
        if isCrypted and self.useGrubVal == 0:
            self.pure = None
            return
        elif isCrypted:
            self.password = val
            self.pure = None
        else:
            salt = "$1$"
            saltLen = 8

            saltchars = string.letters + string.digits + './'
            for i in range(saltLen):
                salt += random.choice(saltchars)

            self.password = crypt.crypt(val, salt)
            self.pure = val
        
    def getPassword (self):
        return self.pure

    def setForceLBA(self, val):
        self.forceLBA32 = val
        
    def setUseGrub(self, val):
        self.useGrubVal = val

    def getPhysicalDevices(self, device):
        # This finds a list of devices on which the given device name resides.
        # Accepted values for "device" are raid1 md devices (i.e. "md0"),
        # physical disks ("hda"), and real partitions on physical disks
        # ("hda1").  Volume groups/logical volumes are not accepted.
        path = self.storage.devicetree.getDeviceByName(device).path[5:]

        if device in map (lambda x: x.name, self.storage.lvs + self.storage.vgs):
            return []

        if path.startswith("mapper/luks-"):
            return []

        if path.startswith('md'):
            bootable = 0
            parts = checkbootloader.getRaidDisks(device, self.storage,
                                                 raidLevel=1, stripPart=0)
            parts.sort()
            return parts

        return [device]

    def runGrubInstall(self, instRoot, bootDev, cmds, cfPath):
        if cfPath == "/":
            syncDataToDisk(bootDev, "/boot", instRoot)
        else:
            syncDataToDisk(bootDev, "/", instRoot)

        # copy the stage files over into /boot
        iutil.execWithRedirect("/sbin/grub-install",
                               ["/sbin/grub-install", "--just-copy"],
                               stdout = "/dev/tty5", stderr = "/dev/tty5",
                               root = instRoot)

        # really install the bootloader
        for cmd in cmds:
            p = os.pipe()
            os.write(p[1], cmd + '\n')
            os.close(p[1])

            # FIXME: hack to try to make sure everything is written
            #        to the disk
            if cfPath == "/":
                syncDataToDisk(bootDev, "/boot", instRoot)
            else:
                syncDataToDisk(bootDev, "/", instRoot)

            iutil.execWithRedirect('/sbin/grub' ,
                                   [ "grub",  "--batch", "--no-floppy",
                                     "--device-map=/boot/grub/device.map" ],
                                   stdin = p[0],
                                   stdout = "/dev/tty5", stderr = "/dev/tty5",
                                   root = instRoot)
            os.close(p[0])

    def installGrub(self, instRoot, bootDevs, grubTarget, grubPath,
                    target, cfPath):
        if iutil.isEfi():
            efiBootloaderInfo.installGrub(self, instRoot, bootDevs, grubTarget,
                                          grubPath, target, cfPath)
            return

        args = "--stage2=/boot/grub/stage2 "
        if self.forceLBA32:
            args = "%s--force-lba " % (args,)

        cmds = []
        for bootDev in bootDevs:
            gtPart = self.getMatchingPart(bootDev, grubTarget)
            gtDisk = self.grubbyPartitionName(getDiskPart(gtPart, self.storage)[0])
            bPart = self.grubbyPartitionName(bootDev)
            cmd = "root %s\n" % (bPart,)

            stage1Target = gtDisk
            if target == "partition":
                stage1Target = self.grubbyPartitionName(gtPart)

            cmd += "install %s%s/stage1 d %s %s/stage2 p %s%s/grub.conf" % \
                (args, grubPath, stage1Target, grubPath, bPart, grubPath)
            cmds.append(cmd)

            self.runGrubInstall(instRoot, bootDev, cmds, cfPath)

    def writeGrub(self, instRoot, bl, kernelList, chainList,
            defaultDev, justConfigFile):

        rootDev = self.storage.fsset.rootDevice

        # XXX old config file should be read here for upgrade

        cf = "%s%s" % (instRoot, self.configfile)
        self.perms = 0600
        if os.access (cf, os.R_OK):
            self.perms = os.stat(cf)[0] & 0777
            os.rename(cf, cf + '.rpmsave')

        grubTarget = bl.getDevice()
        path = self.storage.devicetree.getDeviceByName(grubTarget).path[5:]
        target = "mbr"
        if (path.startswith('rd/') or path.startswith('ida/') or
                path.startswith('cciss/') or
                path.startswith('sx8/') or
                path.startswith('mapper/')):
            if grubTarget[-1].isdigit():
                if grubTarget[-2] == 'p' or \
                        (grubTarget[-2].isdigit() and grubTarget[-3] == 'p'):
                    target = "partition"
        elif grubTarget[-1].isdigit() and not path.startswith('md'):
            target = "partition"

        f = open(cf, "w+")

        f.write("# grub.conf generated by anaconda\n")
        f.write("#\n")
        f.write("# Note that you do not have to rerun grub "
                "after making changes to this file\n")

        try:
            bootDev = self.storage.fsset.mountpoints["/boot"]
            grubPath = "/grub"
            cfPath = "/"
            f.write("# NOTICE:  You have a /boot partition.  This means "
                    "that\n")
            f.write("#          all kernel and initrd paths are relative "
                    "to /boot/, eg.\n")
        except KeyError:
            bootDev = self.storage.fsset.rootDevice
            grubPath = "/boot/grub"
            cfPath = "/boot/"
            f.write("# NOTICE:  You do not have a /boot partition.  "
                    "This means that\n")
            f.write("#          all kernel and initrd paths are relative "
                    "to /, eg.\n")            

        bootDevs = self.getPhysicalDevices(bootDev.name)
        
        f.write('#          root %s\n' % self.grubbyPartitionName(bootDevs[0]))
        f.write("#          kernel %svmlinuz-version ro root=%s\n" % (cfPath, rootDev.path))
        f.write("#          initrd %sinitrd-version.img\n" % (cfPath))
        f.write("#boot=/dev/%s\n" % (grubTarget))

        # get the default image to boot... we have to walk and find it
        # since grub indexes by where it is in the config file
        if defaultDev.name == rootDev.name:
            default = 0
        else:
            # if the default isn't linux, it's the first thing in the
            # chain list
            default = len(kernelList)

        # keep track of which devices are used for the device.map
        usedDevs = {}

        f.write('default=%s\n' % (default))
        f.write('timeout=%d\n' % (self.timeout or 0))

        if self.serial == 1:
            # grub the 0-based number of the serial console device
            unit = self.serialDevice[-1]
            
            # and we want to set the speed too
            speedend = 0
            for char in self.serialOptions:
                if char not in string.digits:
                    break
                speedend = speedend + 1
            if speedend != 0:
                speed = self.serialOptions[:speedend]
            else:
                # reasonable default
                speed = "9600"
                
            f.write("serial --unit=%s --speed=%s\n" %(unit, speed))
            f.write("terminal --timeout=%s serial console\n" % (self.timeout or 5))
        else:
            # we only want splashimage if they're not using a serial console
            if os.access("%s/boot/grub/splash.xpm.gz" %(instRoot,), os.R_OK):
                f.write('splashimage=%s%sgrub/splash.xpm.gz\n'
                        % (self.grubbyPartitionName(bootDevs[0]), cfPath))
                f.write("hiddenmenu\n")

        for dev in self.getPhysicalDevices(grubTarget):
            usedDevs[dev] = 1
            
        if self.password:
            f.write('password --md5 %s\n' %(self.password))
        
        for (label, longlabel, version) in kernelList:
            kernelTag = "-" + version
            kernelFile = "%svmlinuz%s" % (cfPath, kernelTag)

            initrd = self.makeInitrd(kernelTag)

            f.write('title %s (%s)\n' % (longlabel, version))
            f.write('\troot %s\n' % self.grubbyPartitionName(bootDevs[0]))

            realroot = " root=%s" % rootDev.fstabSpec

            if version.endswith("xen0") or (version.endswith("xen") and not os.path.exists("/proc/xen")):
                # hypervisor case
                sermap = { "ttyS0": "com1", "ttyS1": "com2",
                           "ttyS2": "com3", "ttyS3": "com4" }
                if self.serial and sermap.has_key(self.serialDevice) and \
                       self.serialOptions:
                    hvs = "%s=%s" %(sermap[self.serialDevice],
                                    self.serialOptions)
                else:
                    hvs = ""
                if version.endswith("xen0"):
                    hvFile = "%sxen.gz-%s %s" %(cfPath,
                                                version.replace("xen0", ""),
                                                hvs)
                else:
                    hvFile = "%sxen.gz-%s %s" %(cfPath,
                                                version.replace("xen", ""),
                                                hvs)
                f.write('\tkernel %s\n' %(hvFile,))
                f.write('\tmodule %s ro%s' %(kernelFile, realroot))
                if self.args.get():
                    f.write(' %s' % self.args.get())
                f.write('\n')

                if os.access (instRoot + initrd, os.R_OK):
                    f.write('\tmodule %sinitrd%s.img\n' % (cfPath, kernelTag))
            else: # normal kernel
                f.write('\tkernel %s ro%s' % (kernelFile, realroot))
                if self.args.get():
                    f.write(' %s' % self.args.get())
                f.write('\n')

                if os.access (instRoot + initrd, os.R_OK):
                    f.write('\tinitrd %sinitrd%s.img\n' % (cfPath, kernelTag))

        for (label, longlabel, device) in chainList:
            if ((not longlabel) or (longlabel == "")):
                continue
            f.write('title %s\n' % (longlabel))
            f.write('\trootnoverify %s\n' % self.grubbyPartitionName(device))
#            f.write('\tmakeactive\n')
            f.write('\tchainloader +1')
            f.write('\n')
            usedDevs[device] = 1

        f.close()

        if not "/efi/" in cf:
            os.chmod(cf, self.perms)

        try:
            # make symlink for menu.lst (default config file name)
            menulst = "%s%s/menu.lst" % (instRoot, self.configdir)
            if os.access (menulst, os.R_OK):
                os.rename(menulst, menulst + ".rpmsave")
            os.symlink("./grub.conf", menulst)
        except:
            pass

        try:
            # make symlink for /etc/grub.conf (config files belong in /etc)
            etcgrub = "%s%s" % (instRoot, "/etc/grub.conf")
            if os.access (etcgrub, os.R_OK):
                os.rename(etcgrub, etcgrub + ".rpmsave")
            os.symlink(".." + self.configfile, etcgrub)
        except:
            pass
       
        for dev in self.getPhysicalDevices(rootDev.name) + bootDevs:
            usedDevs[dev] = 1

        if os.access(instRoot + "/boot/grub/device.map", os.R_OK):
            os.rename(instRoot + "/boot/grub/device.map",
                      instRoot + "/boot/grub/device.map.rpmsave")

        f = open(instRoot + "/boot/grub/device.map", "w+")
        f.write("# this device map was generated by anaconda\n")
        devs = usedDevs.keys()
        usedDevs = {}
        for dev in devs:
            drive = getDiskPart(dev, self.storage)[0]
            if usedDevs.has_key(drive):
                continue
            usedDevs[drive] = 1
        devs = usedDevs.keys()
        devs.sort()
        for drive in devs:
            # XXX hack city.  If they're not the sort of thing that'll
            # be in the device map, they shouldn't still be in the list.
            path = self.storage.devicetree.getDeviceByName(drive).path
            if not drive.startswith('md'):
                f.write("(%s)     %s\n" % (self.grubbyDiskName(drive), path))
        f.close()

        sysconf = '/etc/sysconfig/grub'
        if os.access (instRoot + sysconf, os.R_OK):
            self.perms = os.stat(instRoot + sysconf)[0] & 0777
            os.rename(instRoot + sysconf,
                      instRoot + sysconf + '.rpmsave')
        # if it's an absolute symlink, just get it out of our way
        elif (os.path.islink(instRoot + sysconf) and
              os.readlink(instRoot + sysconf)[0] == '/'):
            os.rename(instRoot + sysconf,
                      instRoot + sysconf + '.rpmsave')
        f = open(instRoot + sysconf, 'w+')
        f.write("boot=/dev/%s\n" %(grubTarget,))
        # XXX forcelba never gets read back...
        if self.forceLBA32:
            f.write("forcelba=1\n")
        else:
            f.write("forcelba=0\n")
        f.close()
            
        if not justConfigFile:
            self.installGrub(instRoot, bootDevs, grubTarget, grubPath, \
                             target, cfPath)

        return ""

    def getMatchingPart(self, bootDev, target):
        bootName, bootPartNum = getDiskPart(bootDev, self.storage)
        devices = self.getPhysicalDevices(target)
        for device in devices:
            name, partNum = getDiskPart(device, self.storage)
            if name == bootName:
                return device
        return devices[0]

    def grubbyDiskName(self, name):
        return "hd%d" % self.drivelist.index(name)

    def grubbyPartitionName(self, dev):
        (name, partNum) = getDiskPart(dev, self.storage)
        if partNum != None:
            return "(%s,%d)" % (self.grubbyDiskName(name), partNum)
        else:
            return "(%s)" %(self.grubbyDiskName(name))
    

    def getBootloaderConfig(self, instRoot, bl, kernelList,
                            chainList, defaultDev):
        config = bootloaderInfo.getBootloaderConfig(self, instRoot,
                                                    bl, kernelList, chainList,
                                                    defaultDev)

        liloTarget = bl.getDevice()

        config.addEntry("boot", '/dev/' + liloTarget, replace = 0)
        config.addEntry("map", "/boot/map", replace = 0)
        config.addEntry("install", "/boot/boot.b", replace = 0)
        message = "/boot/message"

        if self.pure is not None and not self.useGrubVal:
            config.addEntry("restricted", replace = 0)
            config.addEntry("password", self.pure, replace = 0)

        if self.serial == 1:
           # grab the 0-based number of the serial console device
            unit = self.serialDevice[-1]
            # FIXME: we should probably put some options, but lilo
            # only supports up to 9600 baud so just use the defaults
            # it's better than nothing :(
            config.addEntry("serial=%s" %(unit,))
        else:
            # message screws up serial console
            if os.access(instRoot + message, os.R_OK):
                config.addEntry("message", message, replace = 0)

        if not config.testEntry('lba32'):
            if self.forceLBA32 or (bl.above1024 and
                                   rhpl.getArch() != "x86_64"):
                config.addEntry("lba32", replace = 0)

        return config

    # this is a hackish function that depends on the way anaconda writes
    # out the grub.conf with a #boot= comment
    # XXX this falls into the category of self.doUpgradeOnly
    def upgradeGrub(self, instRoot, bl, kernelList, chainList,
                    defaultDev, justConfigFile):
        if justConfigFile:
            return ""

        theDev = None
        for (fn, stanza) in [ ("/etc/sysconfig/grub", "boot="),
                              ("/boot/grub/grub.conf", "#boot=") ]:
            try:
                f = open(instRoot + fn, "r")
            except:
                continue
        
            # the following bits of code are straight from checkbootloader.py
            lines = f.readlines()
            f.close()
            for line in lines:
                if line.startswith(stanza):
                    theDev = checkbootloader.getBootDevString(line)
                    break
            if theDev is not None:
                break
            
        if theDev is None:
            # we could find the dev before, but can't now...  cry about it
            return ""

        # migrate info to /etc/sysconfig/grub
        self.writeSysconfig(instRoot, theDev)

        # more suckage.  grub-install can't work without a valid /etc/mtab
        # so we have to do shenanigans to get updated grub installed...
        # steal some more code above
        try:
            bootDev = self.storage.fsset.mountpoints["/boot"].name
            grubPath = "/grub"
            cfPath = "/"
        except KeyError:
            bootDev = self.storage.fsset.rootDevice.name
            grubPath = "/boot/grub"
            cfPath = "/boot/"

        masterBootDev = bootDev
        if masterBootDev[0:2] == 'md':
            rootDevs = checkbootloader.getRaidDisks(masterBootDev,
                            self.storage, raidLevel=1, stripPart=0)
        else:
            rootDevs = [masterBootDev]

        if theDev[5:7] == 'md':
            stage1Devs = checkbootloader.getRaidDisks(theDev[5:], self.storage,
                              raidLevel=1)
        else:
            stage1Devs = [theDev[5:]]

        for stage1Dev in stage1Devs:
            # cross fingers; if we can't find a root device on the same
            # hardware as this boot device, we just blindly hope the first
            # thing in the list works.

            grubbyStage1Dev = self.grubbyPartitionName(stage1Dev)

            grubbyRootPart = self.grubbyPartitionName(rootDevs[0])

            for rootDev in rootDevs:
                testGrubbyRootDev = getDiskPart(rootDev, self.storage)[0]
                testGrubbyRootDev = self.grubbyPartitionName(testGrubbyRootDev)

                if grubbyStage1Dev == testGrubbyRootDev:
                    grubbyRootPart = self.grubbyPartitionName(rootDev)
                    break
                    
            args = "--stage2=/boot/grub/stage2 "
            cmd ="root %s" % (grubbyRootPart,)
            cmds = [ cmd ]
            cmd = "install %s%s/stage1 d %s %s/stage2 p %s%s/grub.conf" \
                % (args, grubPath, grubbyStage1Dev, grubPath, grubbyRootPart,
                   grubPath)
            cmds.append(cmd)
        
            if not justConfigFile:
                self.runGrubInstall(instRoot, bootDev, cmds, cfPath)
 
        return ""

    def writeSysconfig(self, instRoot, installDev):
        sysconf = '/etc/sysconfig/grub'
        if not os.access(instRoot + sysconf, os.R_OK):
            f = open(instRoot + sysconf, "w+")
            f.write("boot=%s\n" %(installDev,))
            # XXX forcelba never gets read back at all...
            if self.forceLBA32:
                f.write("forcelba=1\n")
            else:
                f.write("forcelba=0\n")
            f.close()
        
    def write(self, instRoot, bl, kernelList, chainList,
            defaultDev, justConfig):
        if self.timeout is None and chainList:
            self.timeout = 5

        # XXX HACK ALERT - see declaration above
        if self.doUpgradeOnly:
            if self.useGrubVal:
                self.upgradeGrub(instRoot, bl, kernelList,
                                 chainList, defaultDev, justConfig)
            return        

        if len(kernelList) < 1:
            raise BootyNoKernelWarning

        out = self.writeGrub(instRoot, bl, kernelList, 
                             chainList, defaultDev,
                             justConfig | (not self.useGrubVal))

        # XXX move the lilo.conf out of the way if they're using GRUB
        # so that /sbin/installkernel does a more correct thing
        if self.useGrubVal and os.access(instRoot + '/etc/lilo.conf', os.R_OK):
            os.rename(instRoot + "/etc/lilo.conf",
                      instRoot + "/etc/lilo.conf.anaconda")


    def getArgList(self):
        args = bootloaderInfo.getArgList(self)
        
        if self.forceLBA32:
            args.append("--lba32")
        if self.password:
            args.append("--md5pass=%s" %(self.password))
        
        return args

    def __init__(self, storage):
        bootloaderInfo.__init__(self, storage)
        efiBootloaderInfo.__init__(self, storage, initialize=False)

        self._configdir = "/boot/grub"
        self._configname = "grub.conf"
        # XXX use checkbootloader to determine what to default to
        self.useGrubVal = 1
        self.kernelLocation = "/boot/"
        self.password = None
        self.pure = None