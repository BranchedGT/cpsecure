#!/bin/bash
# cp-restore.sh - undoes what cp-harden.sh did, using the saved backup
# run it with: sudo bash cp-restore.sh
# it asks before every single thing

if [ "$EUID" -ne 0 ]; then
  echo "run this with sudo"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
BACKUP=/root/cp-backup

if [ ! -f $BACKUP/.done ]; then
  echo "no backup found in $BACKUP, did you run cp-harden.sh first?"
  exit 1
fi

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

# puts one saved file (or folder) back where it came from
restore_file() {
  if [ -d "$BACKUP$1" ]; then
    cp -a "$BACKUP$1/." "$1/" && echo "restored folder $1"
  elif [ -f "$BACKUP$1" ]; then
    cp -a "$BACKUP$1" "$1" && echo "restored $1"
  else
    echo "no backup of $1, skipping"
    return 1
  fi
}

echo "backup folder: $BACKUP"
echo "files saved:"
(cd $BACKUP && find . -type f ! -name '.done' | sed 's|^\./|  /|' | head -40)


# ---------- 1. ssh ----------
echo
if yes_no "Step 1: restore the original SSH config?"; then
  if restore_file /etc/ssh/sshd_config; then
    systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null
  fi
fi


# ---------- 2. password settings ----------
echo
if yes_no "Step 2: restore the original password settings (login.defs, pwquality, pam)?"; then
  restore_file /etc/login.defs
  restore_file /etc/security/pwquality.conf
  restore_file /etc/pam.d/common-password
fi


# ---------- 3. kernel/network settings ----------
echo
if yes_no "Step 3: undo the network/kernel hardening settings?"; then
  rm -f /etc/sysctl.d/99-cp.conf
  restore_file /etc/sysctl.conf
  sysctl --system >/dev/null 2>&1
  echo "settings file removed. some values only go back to normal after a reboot"
fi


# ---------- 4. login screen ----------
echo
if yes_no "Step 4: undo the guest login / autologin changes?"; then
  rm -f /etc/lightdm/lightdm.conf.d/99-cp.conf
  restore_file /etc/lightdm
  echo "reboot to apply"
fi


# ---------- 5. update settings ----------
echo
if yes_no "Step 5: put the original apt / auto-update config back?"; then
  restore_file /etc/apt/apt.conf.d
  # this file was made by the script if it wasn't in the backup
  if [ ! -e "$BACKUP/etc/apt/apt.conf.d/20auto-upgrades" ]; then
    if yes_no "  20auto-upgrades wasn't there originally, delete it?"; then
      rm -f /etc/apt/apt.conf.d/20auto-upgrades
      echo "deleted"
    fi
  fi
fi


# ---------- 6. sudoers ----------
echo
if yes_no "Step 6: restore the original /etc/sudoers?"; then
  if [ -f $BACKUP/etc/sudoers ]; then
    # check it first so a bad file can't lock us out of sudo
    if visudo -cf $BACKUP/etc/sudoers >/dev/null 2>&1; then
      cp -a $BACKUP/etc/sudoers /etc/sudoers && echo "restored /etc/sudoers"
    else
      echo "backup sudoers failed the syntax check, NOT restoring it"
    fi
  else
    echo "no sudoers backup"
  fi
fi


# ---------- 7. users and passwords ----------
echo
echo "Step 7 is risky: restoring passwd/group/shadow puts the user list and"
echo "passwords back to how they were at the start. any users you deleted or"
echo "passwords you changed since then will be undone."
if yes_no "Step 7: restore /etc/passwd, /etc/group and /etc/shadow?"; then
  restore_file /etc/passwd
  restore_file /etc/group
  restore_file /etc/shadow
  chmod 640 /etc/shadow; chown root:shadow /etc/shadow
  echo "quick consistency check:"
  pwck -r | head -10
  grpck -r | head -10
fi


# ---------- 8. firewall ----------
echo
if [ -f $BACKUP/ufw-before.txt ]; then
  echo "firewall before the script ran:"
  head -3 $BACKUP/ufw-before.txt | sed 's/^/  /'
fi
if yes_no "Step 8: turn the firewall off (ufw disable)?"; then
  ufw disable
  echo "firewall off (the old rules can't be fully restored, ufw reset wiped them)"
fi


# ---------- 9. root account ----------
echo
if [ -f $BACKUP/root-before.txt ]; then
  if grep -qE '^root +P' $BACKUP/root-before.txt; then
    if yes_no "Step 9: root had a password before, unlock the root account again?"; then
      passwd -u root
    fi
  else
    echo "Step 9: root was already locked before, nothing to undo"
  fi
fi


# ---------- 10. reinstall removed packages ----------
echo
if [ -s $BACKUP/removed-packages.txt ]; then
  if yes_no "Step 10: go through the packages the script removed?"; then
    for p in $(sort -u $BACKUP/removed-packages.txt); do
      if yes_no "  reinstall '$p'?"; then
        apt-get install -y "$p"
      fi
    done
  fi
else
  echo "Step 10: the script didn't remove any packages"
fi


# ---------- 11. turn services back on ----------
echo
if [ -s $BACKUP/disabled-services.txt ]; then
  if yes_no "Step 11: go through the services the script disabled?"; then
    for s in $(sort -u $BACKUP/disabled-services.txt); do
      if yes_no "  re-enable '$s'?"; then
        systemctl enable --now "$s"
      fi
    done
  fi
else
  echo "Step 11: the script didn't disable any services"
fi

echo
echo "restore finished. reboot so everything takes effect."
echo "can't be undone by this script: package upgrades, and passwords it"
echo "changed for other users (unless you restored shadow in step 7)."
