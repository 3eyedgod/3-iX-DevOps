node {
    // Naming strings
    def fileName = ''
    def versionName = ''
    def numericName = ''
    def paddedName = ''
    
    // Paths
    def pxeSrcTemplate = "/usr/src/stand/libsa/bootp.bak"
    def pxeSrcTarget = "/usr/src/stand/libsa/bootp.c"
    def pxeOutput = "/usr/obj/usr/src/amd64.amd64/stand/i386/pxeldr/pxeboot"
    def pxeNfsPath = ''
    def pxeHttpPath = ''
    
    // root@dev.prov.pbs.ixsystems.net
    def sshDev = [:]
    sshDev.name = "name"
    sshDev.host = "host"
    sshDev.allowAnyHosts = true
    sshDev.user = 'user'
    sshDev.identityFile = "/var/lib/jenkins/.ssh/id_rsa"
    
    // root@prov.pbs.ixsystems.com
    def sshProd = [:]
    sshProd.name = "name"
    sshProd.host = "host"
    sshProd.allowAnyHosts = true
    sshProd.user = 'root'
    sshProd.identityFile = "/var/lib/jenkins/.ssh/id_rsa"
    
    stage('Download ISO') {
        // Very basic check that URL seems correct
        assert downloadURL.find(/TrueNAS/) == "TrueNAS"
        assert downloadURL.endsWith(".iso")
        
        // Get filename, version string, and verison string filtered to numbers
        fileName = downloadURL.split('/').last()
        versionName = (fileName =~ /TrueNAS-(.+)\.iso/)[0][1]
        numericName = versionName.replaceAll("[^0-9]", "")
        
        // Set Jenkins build name to version string
        currentBuild.displayName = versionName
        
        // Download ISO
        sshCommand remote: sshDev, failOnError: true, command: "curl -O ${downloadURL}"
    }
    stage('Modify ISO files') {
        // Make new directory in NFS share
        pxeNfsPath = "/nfs/truenas/core/${numericName}"
        sshCommand remote: sshDev, failOnError: true, command: "mkdir -p ${pxeNfsPath}"
        
        // Extract ISO to new directory
        sshCommand remote: sshDev, failOnError: true, command: "tar -xf ${fileName} -C ${pxeNfsPath}"
        
        // Comment out the boot from ISO line in loader.conf
        sshCommand remote: sshDev, failOnError: true, command: "gsed -i \"s/vfs.root.mountfrom/#vfs.root.mountfrom/\" ${pxeNfsPath}/boot/loader.conf"
        
        // Fix permissions
        sshCommand remote: sshDev, failOnError: true, command: "chmod -R 755 ${pxeNfsPath}"
    }
    stage('Build custom BSD pxeboot') {
        // Pad the numeric name with null characters to 8 characters for C code
        paddedName = numericName + "\\\\0".multiply(8 - numericName.length())
        sshCommand remote: sshDev, failOnError: true, command: "gsed \"s/ABCDEFGH/${paddedName}/\" ${pxeSrcTemplate} > ${pxeSrcTarget}"
        
        // Build pxeboot
        sshCommand remote: sshDev, failOnError: true, command: "make -C /usr/src/stand"
    }
    stage('Deploy to DEV') {
        // Make new directory in pxe HTTP server
        pxeHttpPath = "/usr/local/www/pxe/images/core/${versionName}"
        sshCommand remote: sshDev, failOnError: true, command: "mkdir -p ${pxeHttpPath}"
        
        // Copy new custom pxeboot to PXELINUX directory
        sshCommand remote: sshDev, failOnError: true, command: "cp -r ${pxeHttpPath} /tftpboot/core/"
        
        // Add new PXELINUX submenu entry at /tftpboot/pxelinux.cfg/menu-core.cfg
        sshCommand remote: sshDev, failOnError: true, command: "gsed -i \"/^menu/a label ${versionName}\\n  menu label TrueNAS Core ${versionName} Installer\\n  pxe core/${versionName}/pxeboot\" /tftpboot/pxelinux.cfg/menu-core.cfg"
        
        // Copy new custom pxeboot to iPXE directory
        sshCommand remote: sshDev, failOnError: true, command: "cp ${pxeOutput} ${pxeHttpPath}"
        
        // Fix permissions
        sshCommand remote: sshDev, failOnError: true, command: "chmod -R 755 ${pxeHttpPath}"
        
        // Add new iPXE menu entry
        sshCommand remote: sshDev, failOnError: true, command: "gsed -i \"/^menu/a item ${versionName}  TrueNAS CORE ${versionName}\" /usr/local/www/pxe/menu-core.ipxe"
    }
    stage('Deploy to PROD') {
        // Copy files from DEV
        sshCommand remote: sshDev, failOnError: true, command: "scp -r ${pxeOutput} root@host:${pxeOutput}"
        sshCommand remote: sshDev, failOnError: true, command: "scp -r ${pxeHttpPath} root@host:${pxeHttpPath}"

        // Make new directory in pxe HTTP server
        pxeHttpPath = "/usr/local/www/pxe/images/core/${versionName}"
        sshCommand remote: sshProd, failOnError: true, command: "mkdir -p ${pxeHttpPath}"
        
        // Copy new custom pxeboot to PXELINUX directory
        sshCommand remote: sshProd, failOnError: true, command: "cp -r ${pxeHttpPath} /tftpboot/core/"
        
        // Add new PXELINUX submenu entry at /tftpboot/pxelinux.cfg/menu-core.cfg
        sshCommand remote: sshProd, failOnError: true, command: "gsed -i \"/^menu/a label ${versionName}\\n  menu label TrueNAS Core ${versionName} Installer\\n  pxe core/${versionName}/pxeboot\" /tftpboot/pxelinux.cfg/menu-core.cfg"
        
        // Copy new custom pxeboot to iPXE directory
        sshCommand remote: sshProd, failOnError: true, command: "cp ${pxeOutput} ${pxeHttpPath}"
        
        // Fix permissions
        sshCommand remote: sshProd, failOnError: true, command: "chmod -R 755 ${pxeHttpPath}"
        
        // Add new iPXE menu entry
        sshCommand remote: sshProd, failOnError: true, command: "gsed -i \"/^menu/a item ${versionName}  TrueNAS CORE ${versionName}\" /usr/local/www/pxe/menu-core.ipxe"
    }
    stage('Clean Up') {
        // Clean up by removing ISO
        sshCommand remote: sshDev, failOnError: true, command: "rm ./${fileName}"
        // Clean up by removing ISO
        sshCommand remote: sshProd, failOnError: true, command: "rm ./${fileName}"
    }
}

