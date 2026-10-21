#!/bin/bash
#Linux Mint CyberPatriot Cleanup
#Run it with: bash cp-harden.sh (it uses sudo when it needs it)
#READ THE README FIRST, don't remove stuff it says you need

#If run with sudo, USER would be root so use the real user instead
USER=${SUDO_USER:-$USER}
LOG=/home/$USER/cp-report.txt
BACKUP=/root/cp-backup
sudo mkdir -p "$BACKUP"
echo "cp run on $(date)" > "$LOG"

#Asks a y/n question, returns 0 for yes
yes_no() {
  local question=$1
  while true; do
    read -rp "$question (y/n): " reply
    case "$reply" in
      [Yy]*) return 0 ;;
      [Nn]*) return 1 ;;
      *) echo "please type y or n" ;;
    esac
  done
}

#Updates a single package, shows old -> new version and asks first
update_pkg() {
  local pkg=$1
  local current
  local candidate

  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "  $pkg isn't installed, skipping"
    return
  fi

  current=$(dpkg-query -W -f='${Version}' "$pkg")
  candidate=$(apt-cache policy "$pkg" | awk '/Candidate:/ {print $2}')

  if [ "$current" = "$candidate" ]; then
    echo "  $pkg is already up to date ($current)"
    return
  fi

  if yes_no "  update $pkg? ($current -> $candidate)"; then
    sudo apt-get install --only-upgrade -y "$pkg"
    echo "updated $pkg to $candidate" | tee -a "$LOG"
  else
    echo "  skipping $pkg...."
  fi
}

#Back up the config files before we touch anything
#Only does it on the first run so a second run can't overwrite the originals
if ! sudo test -f "$BACKUP/.done"; then
  for f in /etc/ssh/sshd_config /etc/login.defs /etc/security/pwquality.conf \
           /etc/pam.d/common-password /etc/sysctl.conf /etc/lightdm \
           /etc/sudoers /etc/passwd /etc/group /etc/shadow \
           /etc/apt/apt.conf.d; do
    [ -e "$f" ] && sudo cp -a --parents "$f" "$BACKUP/" 2>/dev/null
  done
  sudo touch "$BACKUP/.done"
fi
echo "Backups are in $BACKUP (undo with: bash cp-restore.sh)"


#~~~~
#Package Management
#~~~~

#Package update and upgrade
if yes_no "Do you want to refresh the update list and do a full upgrade?"; then
  sudo apt-get update -y
  sudo apt-get full-upgrade -y
  sudo apt-get autoremove -y
  sudo apt-get autoclean -y
  echo "full upgrade done" | tee -a "$LOG"
else
  echo "Skipping full upgrade...."
fi

#Update systemd, chromium and openssh one at a time
if yes_no "Do you want to update systemd, chromium and openssh one at a time?"; then
  sudo apt-get update -qq
  echo "--- systemd ---"
  update_pkg systemd
  echo "--- chromium ---"
  update_pkg chromium
  update_pkg chromium-browser
  #Chromium can also be a snap on some setups
  if command -v snap >/dev/null 2>&1 && snap list chromium >/dev/null 2>&1; then
    if yes_no "  chromium is installed as a snap, refresh it?"; then
      sudo snap refresh chromium
    else
      echo "  skipping chromium snap...."
    fi
  fi
  echo "--- openssh ---"
  update_pkg openssh-client
  update_pkg openssh-server
  update_pkg openssh-sftp-server
else
  echo "Skipping one at a time updates...."
fi

#Install the important security updates
if yes_no "Do you want to install the important security updates?"; then
  sudo apt-get update -y
  #Anything whose newest version comes from a -security repo
  security_updates=$(apt list --upgradable 2>/dev/null | grep -- '-security' | cut -d/ -f1)
  if [ -z "$security_updates" ]; then
    echo "No pending security updates"
  else
    echo "Security updates found:"
    echo "$security_updates" | sed 's/^/  /'
    sudo apt-get install --only-upgrade -y $security_updates
    echo "$security_updates" | sed 's/^/security update: /' >> "$LOG"
  fi
else
  echo "Skipping security updates...."
fi

#Enable automatic updates
if yes_no "Do you want to enable automatic updates (security + regular)?"; then
  sudo apt-get install -y unattended-upgrades

  #Mint's distro id doesn't match ubuntu's repos so the default config does nothing
  #Have to put the ubuntu codename in by hand
  ubuntu_name=$(. /etc/os-release; echo "$UBUNTU_CODENAME")
  if [ -z "$ubuntu_name" ]; then
    ubuntu_name=$(grep -oP '(?<=DISTRIB_CODENAME=).*' /etc/upstream-release/lsb-release 2>/dev/null)
  fi
  echo "Ubuntu base is: $ubuntu_name"

  sudo tee /etc/apt/apt.conf.d/50unattended-upgrades >/dev/null << EOF
Unattended-Upgrade::Allowed-Origins {
    "Ubuntu:${ubuntu_name}";
    "Ubuntu:${ubuntu_name}-security";
    "Ubuntu:${ubuntu_name}-updates";
    "UbuntuESMApps:${ubuntu_name}-apps-security";
    "UbuntuESM:${ubuntu_name}-infra-security";
    "o=linuxmint,a=\${distro_codename}";
};
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF

  sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null << EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF

  sudo systemctl enable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1
  sudo unattended-upgrade --dry-run -d 2>&1 | tail -n 15 >> "$LOG"
  echo "Auto updates on, package list refreshes daily (also check Update Manager settings by hand)" | tee -a "$LOG"
else
  echo "Skipping automatic updates...."
fi

#Remove wireshark
if yes_no "Do you want to remove wireshark?"; then
  sudo apt-get purge -y wireshark wireshark-common wireshark-qt wireshark-gtk tshark 2>/dev/null
  sudo apt-get autoremove -y
  echo "wireshark removed" | tee -a "$LOG"
  echo "wireshark" | sudo tee -a "$BACKUP/removed-packages.txt" >/dev/null
else
  echo "Skipping wireshark removal...."
fi

#Remove unauthorized apps one at a time
if yes_no "Do you want to check for sussy packages (hacking tools, games, torrent clients)?"; then
  echo "Sussy packages..."
  packages="john hydra nmap zenmap ncat netcat netcat-openbsd netcat-traditional nikto aircrack-ng ophcrack medusa kismet ettercap-common ettercap-graphical dsniff crack freeciv aisleriot gnome-mahjongg gnome-mines gnome-sudoku transmission-gtk deluge qbittorrent"
  found=0
  for p in $packages; do
    if dpkg -s "$p" >/dev/null 2>&1; then
      found=1
      if yes_no "  found '$p' - remove it?"; then
        sudo apt-get purge -y "$p"
        echo "removed $p" | tee -a "$LOG"
        echo "$p" | sudo tee -a "$BACKUP/removed-packages.txt" >/dev/null
      else
        echo "kept $p" | tee -a "$LOG"
      fi
    fi
  done
  [ $found -eq 0 ] && echo "None of the usual sussy ones were installed"

  echo
  read -rp "Any other package names to check? (space separated, enter to skip): " extra
  for p in $extra; do
    if dpkg -s "$p" >/dev/null 2>&1; then
      if yes_no "  found '$p' - remove it?"; then
        sudo apt-get purge -y "$p"
        echo "removed $p" | tee -a "$LOG"
        echo "$p" | sudo tee -a "$BACKUP/removed-packages.txt" >/dev/null
      else
        echo "kept $p" | tee -a "$LOG"
      fi
    else
      echo "  $p isn't installed"
    fi
  done
  sudo apt-get autoremove -y
else
  echo "Skipping sussy package check...."
fi


#
#Services, SSH and Firewall
#

#Look for running services you might not need
if yes_no "Do you want to check for running services you might not need?"; then
  for s in avahi-daemon cups cups-browsed bluetooth rpcbind nfs-server smbd nmbd \
           vsftpd proftpd telnetd apache2 nginx mysql postgresql snmpd isc-dhcp-server; do
    if systemctl is-active --quiet "$s" 2>/dev/null; then
      if yes_no "  '$s' is running - stop and disable it? (check the readme)"; then
        sudo systemctl disable --now "$s"
        echo "disabled $s" | tee -a "$LOG"
        echo "$s" | sudo tee -a "$BACKUP/disabled-services.txt" >/dev/null
      else
        echo "left $s running" | tee -a "$LOG"
      fi
    fi
  done
else
  echo "Skipping service check...."
fi

#Turn on the UFW firewall
if yes_no "Do you want to enable the UFW firewall (deny incoming)?"; then
  sudo apt-get install -y ufw
  sudo test -f "$BACKUP/ufw-before.txt" || sudo ufw status verbose 2>&1 | sudo tee "$BACKUP/ufw-before.txt" >/dev/null
  sudo ufw --force reset
  sudo ufw default deny incoming
  sudo ufw default allow outgoing
  sudo ufw logging on
  sudo ufw --force enable
  sudo systemctl enable ufw >/dev/null 2>&1
  sudo ufw status verbose | tee -a "$LOG"
  echo "If the readme needs a service open, use: sudo ufw allow <port>/tcp"
else
  echo "Skipping UFW firewall enable...."
fi

#Deal with SSH
if [ -f /etc/ssh/sshd_config ]; then
  if yes_no "Do you want to deal with SSH?"; then
    if yes_no "  does the readme say SSH is required?"; then
      sudo sed -i -E '/^#?(PermitRootLogin|PermitEmptyPasswords|X11Forwarding|MaxAuthTries|LoginGraceTime)/d' /etc/ssh/sshd_config
      sudo tee -a /etc/ssh/sshd_config >/dev/null << EOF
PermitRootLogin no
PermitEmptyPasswords no
X11Forwarding no
MaxAuthTries 4
LoginGraceTime 60
EOF
      sudo systemctl restart ssh 2>/dev/null || sudo systemctl restart sshd 2>/dev/null
      sudo ufw allow 22/tcp
      echo "SSH locked down and allowed through ufw" | tee -a "$LOG"
    else
      sudo systemctl disable --now ssh 2>/dev/null
      echo "ssh" | sudo tee -a "$BACKUP/disabled-services.txt" >/dev/null
      if yes_no "  also uninstall openssh-server?"; then
        sudo apt-get purge -y openssh-server
        echo "openssh-server" | sudo tee -a "$BACKUP/removed-packages.txt" >/dev/null
      else
        echo "  keeping openssh-server installed...."
      fi
      echo "SSH disabled" | tee -a "$LOG"
    fi
  else
    echo "Skipping SSH...."
  fi
else
  echo "No SSH server installed, skipping SSH...."
fi

#Turn off guest login and autologin
if yes_no "Do you want to turn off guest login and autologin?"; then
  sudo mkdir -p /etc/lightdm/lightdm.conf.d
  sudo tee /etc/lightdm/lightdm.conf.d/99-cp.conf >/dev/null << EOF
[Seat:*]
allow-guest=false
autologin-user=
greeter-hide-users=true
greeter-show-manual-login=true
EOF
  echo "Guest + autologin off (reboot to apply)" | tee -a "$LOG"
else
  echo "Skipping guest and autologin...."
fi


#
#Look at User Accounts
#

#Show users, admins, and weird accounts (changes nothing)
if yes_no "Do you want to see users, admins, and weird accounts (just a list)?"; then
  echo "--- normal users ---" | tee -a "$LOG"
  awk -F: '$3>=1000 && $1!="nobody" {print $1" (uid "$3")"}' /etc/passwd | tee -a "$LOG"
  echo "--- uid 0 accounts (should only be root) ---" | tee -a "$LOG"
  awk -F: '$3==0 {print $1}' /etc/passwd | tee -a "$LOG"
  echo "--- sudo group ---" | tee -a "$LOG"
  getent group sudo | tee -a "$LOG"
  echo "--- empty passwords ---" | tee -a "$LOG"
  sudo awk -F: '$2=="" {print $1}' /etc/shadow | tee -a "$LOG"
  echo "--- NOPASSWD in sudoers ---" | tee -a "$LOG"
  sudo grep -rn "NOPASSWD" /etc/sudoers /etc/sudoers.d/ 2>/dev/null | tee -a "$LOG"
  echo "Compare this to the readme and fix users/admins by hand"
else
  echo "Skipping user list...."
fi

#Lock the root account
if yes_no "Do you want to lock the root account?"; then
  sudo test -f "$BACKUP/root-before.txt" || sudo passwd -S root 2>&1 | sudo tee "$BACKUP/root-before.txt" >/dev/null
  sudo passwd -l root
  echo "Root locked" | tee -a "$LOG"
  echo "Check that you have a working sudo administrator account."
else
  echo "Skipping root lock...."
fi

#Reset passwords for everyone except yourself
if yes_no "Do you want to set a new password for every user except $USER?"; then
  read -rsp "  new password: " new_password
  echo
  for u in $(awk -F: '$3>=1000 && $1!="nobody" {print $1}' /etc/passwd); do
    if [ "$u" = "$USER" ]; then continue; fi
    if yes_no "  change password for $u?"; then
      echo "$u:$new_password" | sudo chpasswd
      echo "changed $u" | tee -a "$LOG"
    else
      echo "  skipping $u...."
    fi
  done
  echo "Don't forget to change your own with: passwd"
else
  echo "Skipping password resets...."
fi


#
#Enable Password requirements and other stuff related to passwords
#

#Set password aging and complexity rules
if yes_no "Do you want to set password aging and complexity rules?"; then
  sudo apt-get install -y libpam-pwquality
  sudo sed -i -E 's/^PASS_MAX_DAYS.*/PASS_MAX_DAYS   90/' /etc/login.defs
  sudo sed -i -E 's/^PASS_MIN_DAYS.*/PASS_MIN_DAYS   7/' /etc/login.defs
  sudo sed -i -E 's/^PASS_WARN_AGE.*/PASS_WARN_AGE   14/' /etc/login.defs

  sudo tee /etc/security/pwquality.conf >/dev/null << EOF
minlen = 10
minclass = 3
maxrepeat = 3
dictcheck = 1
usercheck = 1
EOF

  #Remember last 5 passwords
  if ! grep -q "remember=" /etc/pam.d/common-password; then
    sudo sed -i -E '/pam_unix.so/ s/$/ remember=5/' /etc/pam.d/common-password
  fi
  echo "Password policy set" | tee -a "$LOG"
  echo "(Didn't touch account lockout, do that by hand if the readme wants it)"
else
  echo "Skipping password policy...."
fi

#Fix permissions on passwd, shadow, group
if yes_no "Do you want to fix permissions on passwd, shadow, group?"; then
  sudo chmod 644 /etc/passwd
  sudo chmod 644 /etc/group
  sudo chmod 640 /etc/shadow
  sudo chown root:shadow /etc/shadow
  sudo chmod 640 /etc/gshadow 2>/dev/null
  sudo chmod 600 /boot/grub/grub.cfg 2>/dev/null
  echo "Permissions fixed" | tee -a "$LOG"
else
  echo "Skipping permission fixes...."
fi

#Apply network/kernel hardening settings
if yes_no "Do you want to apply network/kernel hardening settings?"; then
  sudo tee /etc/sysctl.d/99-cp.conf >/dev/null << EOF
net.ipv4.ip_forward = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv6.conf.all.accept_redirects = 0
kernel.randomize_va_space = 2
fs.suid_dumpable = 0
EOF
  sudo sysctl --system >/dev/null 2>&1
  echo "sysctl settings applied" | tee -a "$LOG"
else
  echo "Skipping kernel hardening...."
fi


#
#Look for files that are not allowed
#

#Report only, doesn't delete anything
if yes_no "Do you want to scan for open ports, media files, cron jobs, etc (report only)?"; then
  echo "--- listening ports ---" | tee -a "$LOG"
  sudo ss -tulpn | tee -a "$LOG"

  echo "--- media/picture files in /home ---" | tee -a "$LOG"
  sudo find /home -type f \( -iname '*.mp3' -o -iname '*.mp4' -o -iname '*.avi' -o -iname '*.mkv' \
    -o -iname '*.wav' -o -iname '*.flac' -o -iname '*.mov' -o -iname '*.ogg' \
    -o -iname '*.jpg' -o -iname '*.png' -o -iname '*.gif' \) 2>/dev/null | tee -a "$LOG"

  echo "--- sketchy file names ---" | tee -a "$LOG"
  sudo find / -xdev -type f \( -iname '*crack*' -o -iname '*keylog*' -o -iname '*hack*' -o -iname '*passwords*' \) \
    -not -path '/usr/*' -not -path '/proc/*' -not -path '/snap/*' 2>/dev/null | tee -a "$LOG"

  echo "--- cron jobs ---" | tee -a "$LOG"
  for u in $(cut -d: -f1 /etc/passwd); do
    cron_jobs=$(sudo crontab -l -u "$u" 2>/dev/null)
    if [ -n "$cron_jobs" ]; then echo "[$u]"; echo "$cron_jobs"; fi
  done | tee -a "$LOG"
  cat /etc/crontab | tee -a "$LOG"

  echo "--- enabled services ---" | tee -a "$LOG"
  systemctl list-unit-files --state=enabled --type=service | tee -a "$LOG"
else
  echo "Skipping scan...."
fi


#
#Enable Goof off mode
#
if yes_no "Do you have time to spare?"; then

  #Cool ASCII Drawing
  if yes_no "Do you want to see a cool ASCII drawing?"; then
    echo "Here is a cool ASCII drawing:"
    echo "          .  *  ."
    echo "       *   \ | /   *"
    echo "     .  --  *  --  ."
    echo "       *   / | \   *"
    echo "          '  |  '"
    echo "            \|/"
    echo "             |"
    echo "            / \ "
    echo "This is supposed to be a firework Btw"
  else
    echo "Skipping ASCII drawing...."
    echo "Not Really, You should of said yes"
    echo "....................../´¯/)"
    echo "....................,/¯../"
    echo ".................../..../"
    echo "............./´¯/'...'/´¯¯\`·¸"
    echo "........../'/.../..../......./¨¯\\"
    echo "........('(...´...´.... ¯~/'...')"
    echo ".........\.................'...../"
    echo "..........''...\.......... _.·´"
    echo "............\..............("
    echo "..............\.............\\..."

    #Wanna Delete Everything!!! (fake, nothing actually gets deleted)
    if yes_no "Wanna fully secure your computer?"; then
      echo "You shouldve said no silly goose :) now imma delete EVERYTHING"
      for folder in /home /etc /usr /var /boot /bin; do
        echo "deleting $folder ..."
        sleep 1
      done
      echo "Just kidding, nothing was deleted. Your files are fine :)"
    else
      echo "Good choice, you should of said yes tho"
    fi
  fi

  #
  #Search files for sussy stuff
  #

  #Plant sussy stuff in files without asking for permission
  sudo touch "/SuSsY Malware.txt"
  sudo touch "/SuSsY Virus.txt"

  #Find the sussy stuff i planted in files and listing where they are (its our sussy malware and virus files)
  if yes_no "Do you want to search for sussy stuff in files?"; then
    echo "Searching for sussy stuff in files..."
    sudo find / -xdev -type f \( -name "SuSsY Malware.txt" -o -name "SuSsY Virus.txt" \) 2>/dev/null
    echo "WOW how sussy is that, I found sussy stuff in files!"
  else
    echo "Skipping search for sussy stuff...."
  fi

  #Clean up the sussy stuff so it doesn't hang around
  if yes_no "Do you want to delete the sussy files i planted?"; then
    sudo rm -f "/SuSsY Malware.txt" "/SuSsY Virus.txt"
    echo "Sussy files deleted!"
  else
    echo "Leaving the sussy files...."
  fi
else
  echo "Skipping goof off mode...."
fi

echo
echo "All done. Report is at $LOG"
echo "Still todo by hand: forensics questions, readme users/admins, media files,"
echo "firefox settings, screen lock. then reboot."
