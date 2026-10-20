#!/bin/bash
# cp-harden.sh - linux mint cyberpatriot cleanup
# run it with: sudo bash cp-harden.sh
# READ THE README FIRST, don't remove stuff it says you need

if [ "$EUID" -ne 0 ]; then
  echo "run this with sudo"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
ME=${SUDO_USER:-$USER}
LOG=/home/$ME/cp-report.txt
BACKUP=/root/cp-backup
mkdir -p $BACKUP
echo "cp run on $(date)" > $LOG

# asks a y/n question, returns 0 for yes
yes_no() {
  while true; do
    read -rp "$1 (y/n): " ans
    case "$ans" in
      [Yy]*) return 0 ;;
      [Nn]*) return 1 ;;
      *) echo "just type y or n" ;;
    esac
  done
}

# updates a single package, shows old -> new version and asks first
update_pkg() {
  p=$1
  if ! dpkg -s "$p" >/dev/null 2>&1; then
    echo "  $p isn't installed, skipping"
    return
  fi
  cur=$(dpkg-query -W -f='${Version}' "$p")
  new=$(apt-cache policy "$p" | awk '/Candidate:/ {print $2}')
  if [ "$cur" = "$new" ]; then
    echo "  $p is already up to date ($cur)"
    return
  fi
  if yes_no "  update $p? ($cur -> $new)"; then
    apt-get install --only-upgrade -y "$p"
    echo "updated $p to $new" | tee -a $LOG
  fi
}

# back up the config files before we touch anything
# only does it on the first run so a second run can't overwrite the originals
if [ ! -f $BACKUP/.done ]; then
  for f in /etc/ssh/sshd_config /etc/login.defs /etc/security/pwquality.conf \
           /etc/pam.d/common-password /etc/sysctl.conf /etc/lightdm \
           /etc/sudoers /etc/passwd /etc/group /etc/shadow \
           /etc/apt/apt.conf.d; do
    [ -e "$f" ] && cp -a --parents "$f" $BACKUP/ 2>/dev/null
  done
  touch $BACKUP/.done
fi
echo "backups are in $BACKUP (undo with: sudo bash cp-restore.sh)"


# ---------- 1. updates ----------
echo
if yes_no "Step 1: refresh the update list and do a full upgrade (apt update + apt full-upgrade -y)?"; then
  apt-get update -y
  apt-get full-upgrade -y
  apt-get autoremove -y
  apt-get autoclean -y
  echo "full upgrade done" | tee -a $LOG
fi


# ---------- 2. systemd, chromium, openssh one at a time ----------
echo
if yes_no "Step 2: update systemd, chromium and openssh one at a time?"; then
  apt-get update -qq
  echo "--- systemd ---"
  update_pkg systemd
  echo "--- chromium ---"
  update_pkg chromium
  update_pkg chromium-browser
  # chromium can also be a snap on some setups
  if command -v snap >/dev/null 2>&1 && snap list chromium >/dev/null 2>&1; then
    yes_no "  chromium is installed as a snap, refresh it?" && snap refresh chromium
  fi
  echo "--- openssh ---"
  update_pkg openssh-client
  update_pkg openssh-server
  update_pkg openssh-sftp-server
fi


# ---------- 3. important security updates ----------
echo
if yes_no "Step 3: install the important security updates?"; then
  apt-get update -y
  # anything whose newest version comes from a -security repo
  SEC=$(apt list --upgradable 2>/dev/null | grep -- '-security' | cut -d/ -f1)
  if [ -z "$SEC" ]; then
    echo "no pending security updates"
  else
    echo "security updates found:"
    echo "$SEC" | sed 's/^/  /'
    apt-get install --only-upgrade -y $SEC
    echo "$SEC" | sed 's/^/security update: /' >> $LOG
  fi
fi


# ---------- 4. automatic updates ----------
echo
if yes_no "Step 4: turn on automatic updates (security + regular)?"; then
  apt-get install -y unattended-upgrades

  # mint's distro id doesn't match ubuntu's repos so the default config
  # does nothing, have to put the ubuntu codename in by hand
  UBU=$(. /etc/os-release; echo "$UBUNTU_CODENAME")
  if [ -z "$UBU" ]; then
    UBU=$(grep -oP '(?<=DISTRIB_CODENAME=).*' /etc/upstream-release/lsb-release 2>/dev/null)
  fi
  echo "ubuntu base is: $UBU"

  cat > /etc/apt/apt.conf.d/50unattended-upgrades << EOF
Unattended-Upgrade::Allowed-Origins {
    "Ubuntu:${UBU}";
    "Ubuntu:${UBU}-security";
    "Ubuntu:${UBU}-updates";
    "UbuntuESMApps:${UBU}-apps-security";
    "UbuntuESM:${UBU}-infra-security";
    "o=linuxmint,a=\${distro_codename}";
};
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF

  cat > /etc/apt/apt.conf.d/20auto-upgrades << EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF

  systemctl enable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1
  unattended-upgrade --dry-run -d 2>&1 | tail -n 15 >> $LOG
  echo "auto updates on, package list refreshes daily (also check Update Manager settings by hand)" | tee -a $LOG
fi


# ---------- 5. firewall ----------
echo
if yes_no "Step 5: turn on the firewall (ufw, deny incoming)?"; then
  apt-get install -y ufw
  [ -f $BACKUP/ufw-before.txt ] || ufw status verbose > $BACKUP/ufw-before.txt 2>&1
  ufw --force reset
  ufw default deny incoming
  ufw default allow outgoing
  ufw logging on
  ufw --force enable
  systemctl enable ufw >/dev/null 2>&1
  ufw status verbose | tee -a $LOG
  echo "if the readme needs a service open, use: sudo ufw allow <port>/tcp"
fi


# ---------- 6. wireshark ----------
echo
if yes_no "Step 6: remove wireshark?"; then
  apt-get purge -y wireshark wireshark-common wireshark-qt wireshark-gtk tshark 2>/dev/null
  apt-get autoremove -y
  echo "wireshark removed" | tee -a $LOG
  echo "wireshark" >> $BACKUP/removed-packages.txt
fi


# ---------- 7. unauthorized apps, one at a time ----------
echo
if yes_no "Step 7: check for unauthorized apps (hacking tools, games, torrent clients)?"; then
  APPS="john hydra nmap zenmap ncat netcat netcat-openbsd netcat-traditional nikto aircrack-ng ophcrack medusa kismet ettercap-common ettercap-graphical dsniff crack freeciv aisleriot gnome-mahjongg gnome-mines gnome-sudoku transmission-gtk deluge qbittorrent"
  found=0
  for p in $APPS; do
    if dpkg -s "$p" >/dev/null 2>&1; then
      found=1
      if yes_no "  found '$p' - remove it?"; then
        apt-get purge -y "$p"
        echo "removed $p" | tee -a $LOG; echo "$p" >> $BACKUP/removed-packages.txt
      else
        echo "kept $p" | tee -a $LOG
      fi
    fi
  done
  [ $found -eq 0 ] && echo "none of the usual ones were installed"

  echo
  read -rp "any other package names to check? (space separated, enter to skip): " extra
  for p in $extra; do
    if dpkg -s "$p" >/dev/null 2>&1; then
      if yes_no "  found '$p' - remove it?"; then
        apt-get purge -y "$p"
        echo "removed $p" | tee -a $LOG; echo "$p" >> $BACKUP/removed-packages.txt
      fi
    else
      echo "  $p isn't installed"
    fi
  done
  apt-get autoremove -y
fi


# ---------- 8. services, one at a time ----------
echo
if yes_no "Step 8: check for running services you might not need?"; then
  for s in avahi-daemon cups cups-browsed bluetooth rpcbind nfs-server smbd nmbd \
           vsftpd proftpd telnetd apache2 nginx mysql postgresql snmpd isc-dhcp-server; do
    if systemctl is-active --quiet $s 2>/dev/null; then
      if yes_no "  '$s' is running - stop and disable it? (check the readme)"; then
        systemctl disable --now $s
        echo "disabled $s" | tee -a $LOG; echo "$s" >> $BACKUP/disabled-services.txt
      else
        echo "left $s running" | tee -a $LOG
      fi
    fi
  done
fi


# ---------- 9. ssh ----------
echo
if [ -f /etc/ssh/sshd_config ]; then
  if yes_no "Step 9: deal with SSH?"; then
    if yes_no "  does the readme say SSH is required?"; then
      sed -i -E '/^#?(PermitRootLogin|PermitEmptyPasswords|X11Forwarding|MaxAuthTries|LoginGraceTime)/d' /etc/ssh/sshd_config
      cat >> /etc/ssh/sshd_config << EOF
PermitRootLogin no
PermitEmptyPasswords no
X11Forwarding no
MaxAuthTries 4
LoginGraceTime 60
EOF
      systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null
      ufw allow 22/tcp
      echo "ssh locked down and allowed through ufw" | tee -a $LOG
    else
      systemctl disable --now ssh 2>/dev/null
      echo "ssh" >> $BACKUP/disabled-services.txt
      yes_no "  also uninstall openssh-server?" && { apt-get purge -y openssh-server; echo "openssh-server" >> $BACKUP/removed-packages.txt; }
      echo "ssh disabled" | tee -a $LOG
    fi
  fi
else
  echo "Step 9: no ssh server installed, skipping"
fi


# ---------- 10. login screen ----------
echo
if yes_no "Step 10: turn off guest login and autologin?"; then
  mkdir -p /etc/lightdm/lightdm.conf.d
  cat > /etc/lightdm/lightdm.conf.d/99-cp.conf << EOF
[Seat:*]
allow-guest=false
autologin-user=
greeter-hide-users=true
greeter-show-manual-login=true
EOF
  echo "guest + autologin off (reboot to apply)" | tee -a $LOG
fi


# ---------- 11. password policy ----------
echo
if yes_no "Step 11: set password aging and complexity rules?"; then
  apt-get install -y libpam-pwquality
  sed -i -E 's/^PASS_MAX_DAYS.*/PASS_MAX_DAYS   90/' /etc/login.defs
  sed -i -E 's/^PASS_MIN_DAYS.*/PASS_MIN_DAYS   7/' /etc/login.defs
  sed -i -E 's/^PASS_WARN_AGE.*/PASS_WARN_AGE   14/' /etc/login.defs

  cat > /etc/security/pwquality.conf << EOF
minlen = 10
minclass = 3
maxrepeat = 3
dictcheck = 1
usercheck = 1
EOF

  # remember last 5 passwords
  if ! grep -q "remember=" /etc/pam.d/common-password; then
    sed -i -E '/pam_unix.so/ s/$/ remember=5/' /etc/pam.d/common-password
  fi
  echo "password policy set" | tee -a $LOG
  echo "(didn't touch account lockout, do that by hand if the readme wants it)"
fi


# ---------- 12. lock root ----------
echo
if yes_no "Step 12: lock the root account?"; then
  [ -f $BACKUP/root-before.txt ] || passwd -S root > $BACKUP/root-before.txt 2>&1
  passwd -l root
  echo "root locked" | tee -a $LOG
fi


# ---------- 13. look at users ----------
echo
if yes_no "Step 13: show users, admins, and weird accounts (just a list, changes nothing)?"; then
  echo "--- normal users ---" | tee -a $LOG
  awk -F: '$3>=1000 && $1!="nobody" {print $1" (uid "$3")"}' /etc/passwd | tee -a $LOG
  echo "--- uid 0 accounts (should only be root) ---" | tee -a $LOG
  awk -F: '$3==0 {print $1}' /etc/passwd | tee -a $LOG
  echo "--- sudo group ---" | tee -a $LOG
  getent group sudo | tee -a $LOG
  echo "--- empty passwords ---" | tee -a $LOG
  awk -F: '$2=="" {print $1}' /etc/shadow | tee -a $LOG
  echo "--- NOPASSWD in sudoers ---" | tee -a $LOG
  grep -rn "NOPASSWD" /etc/sudoers /etc/sudoers.d/ 2>/dev/null | tee -a $LOG
  echo "compare this to the readme and fix users/admins by hand"
fi


# ---------- 14. reset passwords ----------
echo
if yes_no "Step 14: set a new password for every user except $ME?"; then
  read -rsp "  new password: " NEWPW
  echo
  for u in $(awk -F: '$3>=1000 && $1!="nobody" {print $1}' /etc/passwd); do
    if [ "$u" = "$ME" ]; then continue; fi
    if yes_no "  change password for $u?"; then
      echo "$u:$NEWPW" | chpasswd
      echo "changed $u" | tee -a $LOG
    fi
  done
  echo "dont forget to change your own with: passwd"
fi


# ---------- 15. file permissions ----------
echo
if yes_no "Step 15: fix permissions on passwd, shadow, group?"; then
  chmod 644 /etc/passwd
  chmod 644 /etc/group
  chmod 640 /etc/shadow
  chown root:shadow /etc/shadow
  chmod 640 /etc/gshadow 2>/dev/null
  chmod 600 /boot/grub/grub.cfg 2>/dev/null
  echo "permissions fixed" | tee -a $LOG
fi


# ---------- 16. kernel/network settings ----------
echo
if yes_no "Step 16: apply network/kernel hardening settings?"; then
  cat > /etc/sysctl.d/99-cp.conf << EOF
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
  sysctl --system >/dev/null 2>&1
  echo "sysctl settings applied" | tee -a $LOG
fi


# ---------- 17. look around (doesn't delete anything) ----------
echo
if yes_no "Step 17: scan for open ports, media files, cron jobs, etc (report only)?"; then
  echo "--- listening ports ---" | tee -a $LOG
  ss -tulpn | tee -a $LOG

  echo "--- media/picture files in /home ---" | tee -a $LOG
  find /home -type f \( -iname '*.mp3' -o -iname '*.mp4' -o -iname '*.avi' -o -iname '*.mkv' \
    -o -iname '*.wav' -o -iname '*.flac' -o -iname '*.mov' -o -iname '*.ogg' \
    -o -iname '*.jpg' -o -iname '*.png' -o -iname '*.gif' \) 2>/dev/null | tee -a $LOG

  echo "--- sketchy file names ---" | tee -a $LOG
  find / -xdev -type f \( -iname '*crack*' -o -iname '*keylog*' -o -iname '*hack*' -o -iname '*passwords*' \) \
    -not -path '/usr/*' -not -path '/proc/*' -not -path '/snap/*' 2>/dev/null | tee -a $LOG

  echo "--- cron jobs ---" | tee -a $LOG
  for u in $(cut -d: -f1 /etc/passwd); do
    c=$(crontab -l -u $u 2>/dev/null)
    if [ -n "$c" ]; then echo "[$u]"; echo "$c"; fi
  done | tee -a $LOG
  cat /etc/crontab | tee -a $LOG

  echo "--- enabled services ---" | tee -a $LOG
  systemctl list-unit-files --state=enabled --type=service | tee -a $LOG
fi

echo
echo "all done. report is at $LOG"
echo "still todo by hand: forensics questions, readme users/admins, media files,"
echo "firefox settings, screen lock. then reboot."
