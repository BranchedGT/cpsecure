#!/bin/bash
#Undoes what cp-harden.sh did, using the saved backup
#Run it with: bash cp-restore.sh (it uses sudo when it needs it)
#It asks before every single thing

BACKUP=/root/cp-backup

if ! sudo test -f "$BACKUP/.done"; then
  echo "No backup found in $BACKUP, did you run cp-harden.sh first?"
  exit 1
fi

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

#Puts one saved file (or folder) back where it came from
restore_file() {
  local path=$1
  if sudo test -d "$BACKUP$path"; then
    sudo cp -a "$BACKUP$path/." "$path/" && echo "Restored folder $path"
  elif sudo test -f "$BACKUP$path"; then
    sudo cp -a "$BACKUP$path" "$path" && echo "Restored $path"
  else
    echo "No backup of $path, skipping...."
    return 1
  fi
}

echo "Backup folder: $BACKUP"
echo "Files saved:"
sudo find "$BACKUP" -type f ! -name '.done' | sed "s|^$BACKUP|  |" | head -40


#
#Undo Config Changes
#

#Restore the original SSH config
if yes_no "Do you want to restore the original SSH config?"; then
  if restore_file /etc/ssh/sshd_config; then
    sudo systemctl restart ssh 2>/dev/null || sudo systemctl restart sshd 2>/dev/null
  fi
else
  echo "Skipping SSH config restore...."
fi

#Restore the original password settings
if yes_no "Do you want to restore the original password settings (login.defs, pwquality, pam)?"; then
  restore_file /etc/login.defs
  restore_file /etc/security/pwquality.conf
  restore_file /etc/pam.d/common-password
else
  echo "Skipping password settings restore...."
fi

#Undo the network/kernel hardening settings
if yes_no "Do you want to undo the network/kernel hardening settings?"; then
  sudo rm -f /etc/sysctl.d/99-cp.conf
  restore_file /etc/sysctl.conf
  sudo sysctl --system >/dev/null 2>&1
  echo "Settings file removed. Some values only go back to normal after a reboot"
else
  echo "Skipping kernel settings undo...."
fi

#Undo the guest login / autologin changes
if yes_no "Do you want to undo the guest login / autologin changes?"; then
  sudo rm -f /etc/lightdm/lightdm.conf.d/99-cp.conf
  restore_file /etc/lightdm
  echo "Reboot to apply"
else
  echo "Skipping login screen undo...."
fi

#Put the original apt / auto-update config back
if yes_no "Do you want to put the original apt / auto-update config back?"; then
  restore_file /etc/apt/apt.conf.d
  #This file was made by the script if it wasn't in the backup
  if ! sudo test -e "$BACKUP/etc/apt/apt.conf.d/20auto-upgrades"; then
    if yes_no "  20auto-upgrades wasn't there originally, delete it?"; then
      sudo rm -f /etc/apt/apt.conf.d/20auto-upgrades
      echo "Deleted"
    else
      echo "  keeping 20auto-upgrades...."
    fi
  fi
else
  echo "Skipping apt config restore...."
fi

#Restore the original sudoers
if yes_no "Do you want to restore the original /etc/sudoers?"; then
  if sudo test -f "$BACKUP/etc/sudoers"; then
    #Check it first so a bad file can't lock us out of sudo
    if sudo visudo -cf "$BACKUP/etc/sudoers" >/dev/null 2>&1; then
      sudo cp -a "$BACKUP/etc/sudoers" /etc/sudoers && echo "Restored /etc/sudoers"
    else
      echo "Backup sudoers failed the syntax check, NOT restoring it"
    fi
  else
    echo "No sudoers backup"
  fi
else
  echo "Skipping sudoers restore...."
fi


#
#Undo User and Password Changes
#

#Restore passwd, group and shadow (risky)
echo
echo "This next one is risky: restoring passwd/group/shadow puts the user list and"
echo "passwords back to how they were at the start. Any users you deleted or"
echo "passwords you changed since then will be undone."
if yes_no "Do you want to restore /etc/passwd, /etc/group and /etc/shadow?"; then
  restore_file /etc/passwd
  restore_file /etc/group
  restore_file /etc/shadow
  sudo chmod 640 /etc/shadow
  sudo chown root:shadow /etc/shadow
  echo "Quick consistency check:"
  sudo pwck -r | head -10
  sudo grpck -r | head -10
else
  echo "Skipping user and password restore...."
fi

#Turn the firewall off
echo
if sudo test -f "$BACKUP/ufw-before.txt"; then
  echo "Firewall before the script ran:"
  sudo head -3 "$BACKUP/ufw-before.txt" | sed 's/^/  /'
fi
if yes_no "Do you want to turn the firewall off (ufw disable)?"; then
  sudo ufw disable
  echo "Firewall off (the old rules can't be fully restored, ufw reset wiped them)"
else
  echo "Skipping firewall disable...."
fi

#Unlock the root account
if sudo test -f "$BACKUP/root-before.txt"; then
  if sudo grep -qE '^root +P' "$BACKUP/root-before.txt"; then
    if yes_no "Root had a password before, unlock the root account again?"; then
      sudo passwd -u root
    else
      echo "Skipping root unlock...."
    fi
  else
    echo "Root was already locked before, nothing to undo"
  fi
fi


#
#Bring Back Packages and Services
#

#Reinstall removed packages
if sudo test -s "$BACKUP/removed-packages.txt"; then
  if yes_no "Do you want to go through the packages the script removed?"; then
    for p in $(sudo sort -u "$BACKUP/removed-packages.txt"); do
      if yes_no "  reinstall '$p'?"; then
        sudo apt-get install -y "$p"
      else
        echo "  skipping $p...."
      fi
    done
  else
    echo "Skipping package reinstall...."
  fi
else
  echo "The script didn't remove any packages"
fi

#Turn disabled services back on
if sudo test -s "$BACKUP/disabled-services.txt"; then
  if yes_no "Do you want to go through the services the script disabled?"; then
    for s in $(sudo sort -u "$BACKUP/disabled-services.txt"); do
      if yes_no "  re-enable '$s'?"; then
        sudo systemctl enable --now "$s"
      else
        echo "  skipping $s...."
      fi
    done
  else
    echo "Skipping service re-enable...."
  fi
else
  echo "The script didn't disable any services"
fi

echo
echo "Restore finished. Reboot so everything takes effect."
echo "Can't be undone by this script: package upgrades, and passwords it"
echo "changed for other users (unless you restored shadow earlier)."
