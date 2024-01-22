def fileName = ''
def versionName = ''
def workPath = ''
def pxeHttpPath = ''

def sshBuild = [:]
sshBuild.name = "name"
sshBuild.host = "host"
sshBuild.allowAnyHosts = true
sshBuild.user = 'user'
sshBuild.identityFile = "/var/lib/jenkins/.ssh/id_rsa"

def sshDev = [:]
sshDev.name = "name"
sshDev.host = "host"
sshDev.allowAnyHosts = true
sshDev.user = 'user'
sshDev.identityFile = "/var/lib/jenkins/.ssh/id_rsa"

def sshProd = [:]
sshProd.name = "name"
sshProd.host = "host"
sshProd.allowAnyHosts = true
sshProd.user = 'user'
sshProd.identityFile = "/var/lib/jenkins/.ssh/id_rsa"

node {
    stage('Download ISO') {
        // Very basic check that URL seems correct
        assert downloadURL.find(/TrueNAS/) == "TrueNAS"
        assert downloadURL.find(/SCALE/) == "SCALE"
        assert downloadURL.endsWith(".iso")
        
        // Get filename, version string, and verison string filtered to numbers
        fileName = downloadURL.split('/').last()
        versionName = (fileName =~ /SCALE-(.+)\.iso/)[0][1]
        
        // Set Jenkins build name to version string
        currentBuild.displayName = versionName
        
        // Download ISO
        sshCommand remote: sshBuild, failOnError: true, command: "curl -O ${downloadURL}"
    }
    stage('Modify ISO files') {
        // Make new directory to work in
        workPath = "/opt/scale/${versionName}"
        sshCommand remote: sshBuild, failOnError: true, command: "mkdir -p ${workPath}"
        
        // Extract necessary files from ISO to new directory
        sshCommand remote: sshBuild, failOnError: true, command: "7z e ${fileName} -aoa -o${workPath} vmlinuz initrd.img TrueNAS-SCALE.update live/filesystem.squashfs"

        // Extract filesystem.squashfs to make edits
        sshCommand remote: sshBuild, failOnError: true, command: "unsquashfs -f -d ${workPath}/squashfs-root ${workPath}/filesystem.squashfs"
        
        // Edit .bash_profile startup script to download TrueNAS-SCALE.update and checksum
        sshCommand remote: sshBuild, failOnError: true, command: "cp /opt/scale/bash_profile ${workPath}/squashfs-root/root/.bash_profile"
        sshCommand remote: sshBuild, failOnError: true, command: "cp /opt/scale/download_scale.sh ${workPath}/squashfs-root/root/download_scale.sh"
        sshCommand remote: sshBuild, failOnError: true, command: "sed -i \"s/SCALEVERSION/${versionName}/g\" ${workPath}/squashfs-root/root/download_scale.sh"
        
        // Rebuild filesystem.squashfs
        sshCommand remote: sshBuild, failOnError: true, command: "mksquashfs ${workPath}/squashfs-root ${workPath}/filesystem.squashfs -noappend"
        
        // Generate MD5 checksum
        sshCommand remote: sshBuild, failOnError: true, command: "md5sum ${workPath}/TrueNAS-SCALE.update > ${workPath}/TrueNAS-SCALE.md5"
        sshCommand remote: sshBuild, failOnError: true, command: "sed -i -r \"s/ .*\\/(.+)/ \\1/g\" ${workPath}/TrueNAS-SCALE.md5"
        
        // Fix permissions
        sshCommand remote: sshBuild, failOnError: true, command: "chmod 755 ${workPath}"
        sshCommand remote: sshBuild, failOnError: true, command: "chmod 644 ${workPath}/*"
        
        // Clean up by removing squashfs-root
        sshCommand remote: sshBuild, failOnError: true, command: "rm -r ${workPath}/squashfs-root"
    }
    stage('Deploy to DEV') {
        // Transfer modified files to DEV HTTP
        sshCommand remote: sshBuild, failOnError: true, command: "scp -r ${workPath} root@host:/usr/local/www/pxe/images/scale/"

        // Make new directory /scale/version name/
        //sshCommand remote: sshDev, failOnError: true, command: "mkdir /tftpboot/scale/${versionName}"
        
        // Copy new vmlinuz 
        sshCommand remote: sshDev, failOnError: true, command: "cp /usr/local/www/pxe/images/scale/${versionName}/vmlinuz /tftpboot/scale/${versionName}/"

        // Copy new initrd
        sshCommand remote: sshDev, failOnError: true, command: "cp /usr/local/www/pxe/images/scale/${versionName}/initrd.img /tftpboot/scale/${versionName}/"

        // Add new PXELINUX submenu entry at /tftpboot/pxelinux.cfg/menu-scale.cfg
        sshCommand remote: sshDev, failOnError: true, command: "gsed -i \"/^menu/a label scale-${versionName}\\n  menu label TrueNAS SCALE ${versionName}\\n  kernel scale/${versionName}/vmlinuz\\n  append nomodeset quiet boot=live initrd=scale/${versionName}/initrd.img fetch=http://{ip}/images/scale/${versionName}/filesystem.squashfs\" /tftpboot/pxelinux.cfg/menu-scale.cfg"
 
        // Add new iPXE menu entry
        sshCommand remote: sshDev, failOnError: true, command: "gsed -i \"/^menu/a item ${versionName}  TrueNAS SCALE ${versionName}\" /usr/local/www/pxe/menu-scale.ipxe"
    }
    stage('Deploy to PROD') {
        // Transfer modified files to DEV HTTP
        sshCommand remote: sshBuild, failOnError: true, command: "scp -r ${workPath} root@host:/usr/local/www/pxe/images/scale/"

        // Make new directory /scale/version name/
        sshCommand remote: sshProd, failOnError: false, command: "mkdir /tftpboot/scale/${versionName}"
        
        // Copy new vmlinuz 
        sshCommand remote: sshProd, failOnError: true, command: "cp -r /usr/local/www/pxe/images/scale/${versionName}/vmlinuz /tftpboot/scale/${versionName}/"

        // Copy new initrd
        sshCommand remote: sshProd, failOnError: true, command: "cp -r /usr/local/www/pxe/images/scale/${versionName}/initrd.img /tftpboot/scale/${versionName}/"

        // Add new PXELINUX submenu entry at /tftpboot/pxelinux.cfg/menu-scale.cfg
        sshCommand remote: sshProd, failOnError: true, command: "gsed -i \"/^menu/a label scale-${versionName}\\n  menu label TrueNAS SCALE ${versionName}\\n  kernel scale/${versionName}/vmlinuz\\n  append nomodeset quiet boot=live initrd=scale/${versionName}/initrd.img fetch=http://{ip}/images/scale/${versionName}/filesystem.squashfs\" /tftpboot/pxelinux.cfg/menu-scale.cfg"
 
        // Add new iPXE menu entry
        sshCommand remote: sshProd, failOnError: true, command: "gsed -i \"/^menu/a item ${versionName}  TrueNAS SCALE ${versionName}\" /usr/local/www/pxe/menu-scale.ipxe"
    }
    stage('Clean Up') {
         // Clean up by removing ISO and workpath
        sshCommand remote: sshBuild, failOnError: true, command: "rm ./${fileName}"
        sshCommand remote: sshBuild, failOnError: true, command: "rm -r ${workPath}"
    }
}