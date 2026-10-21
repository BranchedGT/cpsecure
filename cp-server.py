# cp-server.py - windows server cyberpatriot cleanup (the simple version)
# run it as administrator. to turn it into an exe:
#   pip install pyinstaller
#   pyinstaller --onefile --uac-admin cp-server.py
# READ THE README FIRST, don't remove roles/services it says the server needs

import ctypes
import json
import os
import subprocess
import sys
from getpass import getpass

ME = os.environ.get("USERNAME", "")
BACKUP = r"C:\cp-backup"
HOME = os.path.expanduser("~")
DESK = os.path.join(HOME, "Desktop")
LOG = os.path.join(DESK if os.path.isdir(DESK) else HOME, "cp-report.txt")
DEFAULT_ACCOUNTS = {"administrator", "defaultaccount", "wdagutilityaccount", "guest"}


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def log(msg):
    print(msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def yes_no(q):
    while True:
        a = input(q + " (y/n): ").strip().lower()
        if a.startswith("y"):
            return True
        if a.startswith("n"):
            return False
        print("just type y or n")


def step(n, text):
    print()
    return yes_no("Step %d: %s" % (n, text))


def run(cmd, show=True):
    # a string runs through cmd.exe, a list runs directly
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                       text=True, errors="replace")
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if show and out:
        print(out)
    return r.returncode, out


def ps(cmd, show=True):
    return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", cmd], show)


def reg_set(key, name, value, kind="REG_DWORD"):
    rc, out = run(["reg", "add", key, "/v", name, "/t", kind,
                   "/d", str(value), "/f"], show=False)
    return rc == 0


def note(filename, text):
    with open(os.path.join(BACKUP, filename), "a", encoding="utf-8") as f:
        f.write(text + "\n")


def backup():
    os.makedirs(BACKUP, exist_ok=True)
    if os.path.exists(os.path.join(BACKUP, ".done")):
        print("backup already in %s, leaving the originals alone" % BACKUP)
        return
    run(["netsh", "advfirewall", "export", BACKUP + r"\firewall.wfw"], show=False)
    run(["secedit", "/export", "/cfg", BACKUP + r"\secpol-original.inf"], show=False)
    run('net accounts > "%s\\accounts.txt"' % BACKUP, show=False)
    run('net user > "%s\\users.txt"' % BACKUP, show=False)
    run('net localgroup administrators > "%s\\admins.txt"' % BACKUP, show=False)
    ps("Get-Service | Select-Object Name,Status,StartType | "
       "Export-Csv '%s\\services.csv' -NoTypeInformation" % BACKUP, show=False)
    keys = [
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies",
        r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
        r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa",
        r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server",
        r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters",
    ]
    for i, k in enumerate(keys):
        run(["reg", "export", k, "%s\\reg%d.reg" % (BACKUP, i), "/y"], show=False)
    # remember what kind of machine this backup came from (the restore script reads this)
    rc, out = ps("$o = Get-CimInstance Win32_OperatingSystem; '' + $o.ProductType + '|' + $o.Caption", show=False)
    last = out.strip().splitlines()[-1] if out.strip() else ""
    ptype, _, caption = last.partition("|")
    kind = "server" if ptype in ("2", "3") else "windows"
    with open(os.path.join(BACKUP, "backup-info.txt"), "w", encoding="utf-8") as f:
        f.write("type=%s\nproducttype=%s\nos=%s\n" % (kind, ptype, caption.strip()))
    open(os.path.join(BACKUP, ".done"), "w").close()
    print("backups saved in " + BACKUP)


def set_secpol(changes):
    # edits the local security policy (needed for password complexity)
    inf = BACKUP + r"\secpol-new.inf"
    run(["secedit", "/export", "/cfg", inf], show=False)
    try:
        with open(inf, encoding="utf-16") as f:
            lines = f.read().splitlines()
    except Exception as e:
        print("couldn't read the security policy export:", e)
        return
    found = set()
    for i, l in enumerate(lines):
        key = l.split("=")[0].strip()
        if key in changes:
            lines[i] = "%s = %s" % (key, changes[key])
            found.add(key)
    if "[System Access]" in lines:
        idx = lines.index("[System Access]") + 1
        for k, v in changes.items():
            if k not in found:
                lines.insert(idx, "%s = %s" % (k, v))
    with open(inf, "w", encoding="utf-16", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    run(["secedit", "/configure", "/db", BACKUP + r"\secpol.sdb",
         "/cfg", inf, "/areas", "SECURITYPOLICY"])


def local_users():
    rc, out = ps("Get-LocalUser | ForEach-Object { $_.Name + '|' + $_.Enabled }",
                 show=False)
    users = []
    for line in out.splitlines():
        if "|" in line:
            name, en = line.strip().split("|")
            users.append((name, en.lower() == "true"))
    return users


def admins():
    rc, out = ps("Get-LocalGroupMember -SID S-1-5-32-544 | "
                 "ForEach-Object { $_.Name.Split('\\')[-1] }", show=False)
    return [l.strip() for l in out.splitlines() if l.strip()]


def installed_programs():
    cmd = (r"$p='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',"
           r"'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'; "
           "Get-ItemProperty $p -ErrorAction SilentlyContinue | "
           "Where-Object { $_.DisplayName } | "
           "Select-Object DisplayName,UninstallString,QuietUninstallString | "
           "ConvertTo-Json -Compress")
    rc, out = ps(cmd, show=False)
    try:
        data = json.loads(out[out.index("["):] if "[" in out else out[out.index("{"):])
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    seen, apps = set(), []
    for a in data:
        if a["DisplayName"] not in seen:
            seen.add(a["DisplayName"])
            apps.append(a)
    return sorted(apps, key=lambda a: a["DisplayName"].lower())


def uninstall(app):
    cmd = app.get("QuietUninstallString") or app.get("UninstallString")
    if not cmd:
        print("  no uninstall command found, remove it in Control Panel > Programs")
        return
    if "msiexec" in cmd.lower():
        cmd = cmd.replace("/I{", "/X{").replace("/i{", "/X{")
        if "/qn" not in cmd.lower():
            cmd += " /qn /norestart"
    print("  running: " + cmd)
    subprocess.run(cmd, shell=True)
    log("ran uninstall for " + app["DisplayName"])
    note("removed-programs.txt", app["DisplayName"])


def main():
    if not is_admin():
        print("run this as administrator (right click > run as administrator)")
        input("press enter to close")
        sys.exit(1)

    with open(LOG, "w", encoding="utf-8") as f:
        f.write("cp-server run\n")
    backup()

    # domain controllers have no local users, they use active directory instead
    rc, out = ps("(Get-CimInstance Win32_ComputerSystem).DomainRole", show=False)
    is_dc = out.strip() in ("4", "5")
    if is_dc:
        log("this machine is a DOMAIN CONTROLLER - the user steps will be skipped,")
        log("use 'Active Directory Users and Computers' for users and admin groups")

    # ---------- 1. updates ----------
    if step(1, "check for and install all windows updates now?"):
        run("sc config wuauserv start= auto", show=False)
        run("net start wuauserv", show=False)
        ps("$s = New-Object -ComObject Microsoft.Update.Session; "
           "$res = $s.CreateUpdateSearcher().Search(\"IsInstalled=0 and Type='Software' and IsHidden=0\"); "
           "if ($res.Updates.Count -eq 0) { 'no updates found' } else { "
           "$res.Updates | ForEach-Object { 'found: ' + $_.Title }; "
           "$c = New-Object -ComObject Microsoft.Update.UpdateColl; "
           "$res.Updates | ForEach-Object { if (-not $_.EulaAccepted) { $_.AcceptEula() }; [void]$c.Add($_) }; "
           "$d = $s.CreateUpdateDownloader(); $d.Updates = $c; [void]$d.Download(); "
           "$i = $s.CreateUpdateInstaller(); $i.Updates = $c; $r = $i.Install(); "
           "'result code: ' + $r.ResultCode + '  reboot needed: ' + $r.RebootRequired }")
        log("update check finished")

    # ---------- 2. automatic updates ----------
    if step(2, "turn on automatic updates?"):
        k = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
        reg_set(k, "NoAutoUpdate", 0)
        reg_set(k, "AUOptions", 4)
        run("sc config wuauserv start= auto", show=False)
        log("automatic updates on")

    # ---------- 3. defender ----------
    if step(3, "make sure windows defender is on and up to date?"):
        run(["reg", "delete", r"HKLM\SOFTWARE\Policies\Microsoft\Windows Defender",
             "/v", "DisableAntiSpyware", "/f"], show=False)
        ps("Set-MpPreference -DisableRealtimeMonitoring $false -DisableBehaviorMonitoring $false "
           "-DisableIOAVProtection $false")
        ps("Update-MpSignature")
        log("defender settings applied")

    # ---------- 4. firewall ----------
    if step(4, "turn on the firewall for all profiles (block incoming)?"):
        run("netsh advfirewall set allprofiles state on")
        run("netsh advfirewall set allprofiles firewallpolicy blockinbound,allowoutbound")
        run("netsh advfirewall set allprofiles logging droppedconnections enable")
        log("firewall on, incoming blocked")
        print("if the readme says a service must stay reachable (web, dns, rdp...), add an allow rule for it")

    # ---------- 5. password policy ----------
    if step(5, "set password and lockout rules?"):
        run("net accounts /minpwlen:10 /maxpwage:90 /minpwage:7 /uniquepw:5 "
            "/lockoutthreshold:5 /lockoutduration:30 /lockoutwindow:30")
        set_secpol({"PasswordComplexity": 1, "ClearTextPassword": 0})
        log("password policy set")
        if is_dc:
            print("(on a DC the domain password policy is in Group Policy, check it there too)")

    # ---------- 6. guest account ----------
    if step(6, "disable the guest account?"):
        run(["net", "user", "Guest", "/active:no"])
        log("guest disabled")

    # ---------- 7. users one at a time ----------
    if is_dc:
        print("\nStep 7 and 8 skipped (domain controller)")
    else:
        if step(7, "go through the local users one at a time?"):
            adm = [a.lower() for a in admins()]
            for name, enabled in local_users():
                if name.lower() in DEFAULT_ACCOUNTS:
                    continue
                is_adm = name.lower() in adm
                print("\nuser: %s   enabled: %s   admin: %s" % (name, enabled, is_adm))
                if name.lower() == ME.lower():
                    print("  that's you, skipping")
                    continue
                if yes_no("  delete user %s?" % name):
                    run(["net", "user", name, "/delete"])
                    log("deleted user " + name)
                    continue
                if is_adm and yes_no("  remove %s from Administrators?" % name):
                    run(["net", "localgroup", "Administrators", name, "/delete"])
                    log("removed %s from administrators" % name)
            print("\nadmins right now:")
            run(["net", "localgroup", "Administrators"])
            print("compare this to the readme")

        # ---------- 8. passwords ----------
        if step(8, "set a new password for the other users?"):
            pw = getpass("  new password (typing is hidden): ")
            for name, enabled in local_users():
                if name.lower() in DEFAULT_ACCOUNTS or name.lower() == ME.lower():
                    continue
                if yes_no("  change password for %s?" % name):
                    run(["net", "user", name, pw])
                    ps("Set-LocalUser -Name '%s' -PasswordNeverExpires $false" % name, show=False)
                    log("changed password for " + name)
            print("don't forget to change your own: net user %s *" % ME)

    # ---------- 9. roles and features ----------
    if step(9, "check risky server roles/features (telnet, tftp, snmp, smb1, ftp, iis...), one at a time?"):
        feats = [("Telnet-Client", "Telnet client"), ("TFTP-Client", "TFTP client"),
                 ("SNMP-Service", "SNMP service"), ("FS-SMB1", "SMB 1.0"),
                 ("Web-Ftp-Server", "FTP server"), ("Web-Server", "IIS web server"),
                 ("SMTP-Server", "SMTP server"), ("RemoteAccess", "Routing and Remote Access"),
                 ("Print-Server", "Print server")]
        for fname, label in feats:
            rc, out = ps("(Get-WindowsFeature -Name %s).Installed" % fname, show=False)
            if out.strip() == "True":
                if yes_no("  %s is installed - remove it? (check the readme, the server might need it)" % label):
                    ps("Uninstall-WindowsFeature -Name %s" % fname)
                    log("removed feature " + label)
                    note("removed-features.txt", fname)
            else:
                print("  %s: not installed" % label)
        print("removed features may need a reboot to finish")

    # ---------- 10. unauthorized software ----------
    if step(10, "check for unauthorized software, one program at a time?"):
        bad = ["wireshark", "npcap", "nmap", "zenmap", "cain", "ophcrack", "aircrack",
               "netcat", "nikto", "metasploit", "hydra", "john the ripper", "utorrent",
               "bittorrent", "qbittorrent", "vuze", "deluge", "teamviewer", "anydesk",
               "tightvnc", "ultravnc", "realvnc", "keylogger"]
        apps = installed_programs()
        if not apps:
            print("couldn't read the installed programs list, use Control Panel > Programs instead")
        found = 0
        for a in apps:
            if any(b in a["DisplayName"].lower() for b in bad):
                found += 1
                if yes_no("  found '%s' - uninstall it?" % a["DisplayName"]):
                    uninstall(a)
                else:
                    log("kept " + a["DisplayName"])
        if found == 0:
            print("none of the usual suspects were installed")
        extra = input("\n  another program name to look for? (part of the name, enter to skip): ").strip().lower()
        if extra:
            for a in apps:
                if extra in a["DisplayName"].lower():
                    if yes_no("  found '%s' - uninstall it?" % a["DisplayName"]):
                        uninstall(a)
        print("note: some uninstallers open a window, finish them before moving on")

    # ---------- 11. services ----------
    if step(11, "check for services you might not need, one at a time?"):
        svcs = ["RemoteRegistry", "TlntSvr", "SNMP", "SSDPSRV", "upnphost", "FTPSVC",
                "W3SVC", "Browser", "lltdsvc", "Fax", "simptcp", "SharedAccess",
                "Spooler", "WinRM", "LanmanServer"]
        for s in svcs:
            rc, out = run(["sc", "qc", s], show=False)
            if rc != 0 or "DISABLED" in out:
                continue
            if yes_no("  service '%s' is set to run - stop and disable it? (check the readme)" % s):
                run(["sc", "stop", s], show=False)
                run(["sc", "config", s, "start=", "disabled"])
                log("disabled service " + s)
                note("disabled-services.txt", s)
            else:
                log("left service %s alone" % s)

    # ---------- 12. remote desktop ----------
    if step(12, "deal with remote desktop?"):
        ts = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"
        if yes_no("  does the readme say remote desktop is REQUIRED?"):
            reg_set(ts + r"\WinStations\RDP-Tcp", "UserAuthentication", 1)
            log("RDP kept, network level authentication required")
        else:
            reg_set(ts, "fDenyTSConnections", 1)
            run('netsh advfirewall firewall set rule group="remote desktop" new enable=No', show=False)
            log("remote desktop disabled")

    # ---------- 13. security settings ----------
    if step(13, "go through a few security registry settings one at a time?"):
        pol = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        lsa = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
        settings = [
            ("turn UAC on", pol, "EnableLUA", 1),
            ("require ctrl+alt+del to log in", pol, "DisableCAD", 0),
            ("don't show the last username at login", pol, "DontDisplayLastUserName", 1),
            ("turn off autorun on all drives", r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer", "NoDriveTypeAutoRun", 255),
            ("block anonymous listing of accounts and shares", lsa, "RestrictAnonymous", 1),
            ("block anonymous SAM enumeration", lsa, "RestrictAnonymousSAM", 1),
            ("block blank passwords over the network", lsa, "LimitBlankPasswordUse", 1),
            ("don't store old LM password hashes", lsa, "NoLMHash", 1),
            ("turn off the SMBv1 server", r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters", "SMB1", 0),
        ]
        for label, key, name, val in settings:
            if yes_no("  %s?" % label):
                if reg_set(key, name, val):
                    log("set: " + label)
                else:
                    log("FAILED: " + label)

    # ---------- 14. look around (changes nothing) ----------
    if step(14, "scan for open ports, shares, tasks, media files (report only)?"):
        log("--- listening ports ---")
        rc, out = ps("Get-NetTCPConnection -State Listen | Sort-Object LocalPort | ForEach-Object { "
                     "'' + $_.LocalAddress + ':' + $_.LocalPort + '   ' + "
                     "(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName }", show=False)
        log(out)

        log("--- network shares ---")
        rc, out = run("net share", show=False)
        log(out)

        log("--- installed roles/features ---")
        rc, out = ps("Get-WindowsFeature | Where-Object { $_.Installed } | ForEach-Object { $_.Name }", show=False)
        log(out)

        log("--- scheduled tasks (non microsoft) ---")
        rc, out = ps("Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft*' } | "
                     "Select-Object TaskName,State | Format-Table -AutoSize | Out-String -Width 200", show=False)
        log(out)

        log("--- media files in C:\\Users ---")
        exts = (".mp3", ".mp4", ".avi", ".mkv", ".wav", ".flac", ".mov", ".ogg", ".wma", ".wmv")
        for root, dirs, files in os.walk("C:\\Users"):
            dirs[:] = [d for d in dirs if d.lower() != "appdata"]
            for fn in files:
                if fn.lower().endswith(exts):
                    log(os.path.join(root, fn))

    print("\nall done. report is at " + LOG)
    print("still todo by hand: forensics questions, readme users/admins, then reboot.")
    input("press enter to close")


if __name__ == "__main__":
    main()
