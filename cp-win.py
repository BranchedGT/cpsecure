# cp-win.py - windows 11 cyberpatriot cleanup helper
# run it as administrator. to turn it into an exe:
#   pip install pyinstaller
#   pyinstaller --onefile --uac-admin cp-win.py
# READ THE README FIRST, don't remove or disable stuff it says you need

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
    # keeps a list of what we changed, handy for undoing stuff later
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


DEFAULT_ACCOUNTS = {"administrator", "defaultaccount", "wdagutilityaccount", "guest"}


def installed_programs():
    cmd = (r"$p='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',"
           r"'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',"
           r"'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'; "
           "Get-ItemProperty $p -ErrorAction SilentlyContinue | "
           "Where-Object { $_.DisplayName } | "
           "Select-Object DisplayName,DisplayVersion,UninstallString,QuietUninstallString | "
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
        print("  no uninstall command found, remove it in Settings > Apps")
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
        f.write("cp-win run\n")
    backup()

    # ---------- 1. windows update ----------
    if step(1, "check for and install all windows updates now?"):
        run("sc config wuauserv start= auto", show=False)
        run("net start wuauserv", show=False)
        run("sc config bits start= delayed-auto", show=False)
        run("net start bits", show=False)
        ps("$s = New-Object -ComObject Microsoft.Update.Session; "
           "$res = $s.CreateUpdateSearcher().Search(\"IsInstalled=0 and Type='Software' and IsHidden=0\"); "
           "if ($res.Updates.Count -eq 0) { 'no updates found' } else { "
           "$res.Updates | ForEach-Object { 'found: ' + $_.Title }; "
           "$c = New-Object -ComObject Microsoft.Update.UpdateColl; "
           "$res.Updates | ForEach-Object { if (-not $_.EulaAccepted) { $_.AcceptEula() }; [void]$c.Add($_) }; "
           "$d = $s.CreateUpdateDownloader(); $d.Updates = $c; [void]$d.Download(); "
           "$i = $s.CreateUpdateInstaller(); $i.Updates = $c; $r = $i.Install(); "
           "'result code: ' + $r.ResultCode + '  reboot needed: ' + $r.RebootRequired }")
        log("update check finished (also look at Settings > Windows Update)")

    # ---------- 2. automatic updates ----------
    if step(2, "turn on automatic updates?"):
        k = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
        reg_set(k, "NoAutoUpdate", 0)
        reg_set(k, "AUOptions", 4)
        run("sc config wuauserv start= auto", show=False)
        log("automatic updates on")

    # ---------- 3. defender ----------
    if step(3, "make sure windows defender is on and up to date?"):
        # remove policies that turn defender off
        run(["reg", "delete", r"HKLM\SOFTWARE\Policies\Microsoft\Windows Defender",
             "/v", "DisableAntiSpyware", "/f"], show=False)
        run(["reg", "delete", r"HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\Real-Time Protection",
             "/v", "DisableRealtimeMonitoring", "/f"], show=False)
        ps("Set-MpPreference -DisableRealtimeMonitoring $false -DisableBehaviorMonitoring $false "
           "-DisableIOAVProtection $false -DisableScriptScanning $false -PUAProtection Enabled")
        ps("Update-MpSignature")
        log("defender settings applied")
        print("current defender exclusions:")
        ps("Get-MpPreference | Select-Object ExclusionPath,ExclusionExtension,ExclusionProcess | Format-List")
        if yes_no("  remove all defender exclusions?"):
            ps("$p = Get-MpPreference; "
               "$p.ExclusionPath | ForEach-Object { Remove-MpPreference -ExclusionPath $_ }; "
               "$p.ExclusionExtension | ForEach-Object { Remove-MpPreference -ExclusionExtension $_ }; "
               "$p.ExclusionProcess | ForEach-Object { Remove-MpPreference -ExclusionProcess $_ }")
            log("defender exclusions removed")
        if yes_no("  run a quick scan now?"):
            ps("Start-MpScan -ScanType QuickScan")

    # ---------- 4. firewall ----------
    if step(4, "turn on the firewall for all profiles (block incoming)?"):
        run("netsh advfirewall set allprofiles state on")
        run("netsh advfirewall set allprofiles firewallpolicy blockinbound,allowoutbound")
        run("netsh advfirewall set allprofiles logging droppedconnections enable")
        log("firewall on, incoming blocked")

    # ---------- 5. password policy ----------
    if step(5, "set password and lockout rules?"):
        run("net accounts /minpwlen:10 /maxpwage:90 /minpwage:7 /uniquepw:5 "
            "/lockoutthreshold:5 /lockoutduration:30 /lockoutwindow:30")
        set_secpol({"PasswordComplexity": 1, "ClearTextPassword": 0})
        log("password policy set (min 10, complexity on, 90 day max, lockout after 5)")

    # ---------- 6. guest and admin accounts ----------
    if step(6, "disable the guest account?"):
        run(["net", "user", "Guest", "/active:no"])
        log("guest disabled")
    if yes_no("  also disable the built-in Administrator account? (only if the readme has another admin)"):
        run(["net", "user", "Administrator", "/active:no"])
        log("built-in Administrator disabled")

    # ---------- 7. review users one at a time ----------
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
        print("compare this to the readme, and check for admins that aren't listed as users")

    # ---------- 8. reset passwords ----------
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

    # ---------- 9. unauthorized software ----------
    if step(9, "check for unauthorized software, one program at a time?"):
        bad = ["wireshark", "npcap", "nmap", "zenmap", "cain", "ophcrack", "aircrack",
               "netcat", "nikto", "metasploit", "burp suite", "hydra", "john the ripper",
               "utorrent", "bittorrent", "qbittorrent", "vuze", "deluge", "bitcomet",
               "frostwire", "limewire", "teamviewer", "anydesk", "tightvnc", "ultravnc",
               "realvnc", "keylogger", "angry ip", "advanced ip scanner"]
        apps = installed_programs()
        if not apps:
            print("couldn't read the installed programs list, use Settings > Apps instead")
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
        if apps and yes_no("\n  list ALL installed programs so you can pick more?"):
            for i, a in enumerate(apps, 1):
                print("  %3d. %s" % (i, a["DisplayName"]))
            picks = input("  numbers to remove, separated by commas (enter to skip): ")
            for p in picks.split(","):
                p = p.strip()
                if p.isdigit() and 1 <= int(p) <= len(apps):
                    a = apps[int(p) - 1]
                    if yes_no("  uninstall '%s'?" % a["DisplayName"]):
                        uninstall(a)
        print("note: some uninstallers open a window, finish them before moving on")

    # ---------- 10. risky windows features ----------
    if step(10, "check risky windows features (smb1, telnet, tftp, iis), one at a time?"):
        feats = [("SMB1Protocol", "SMB 1.0 file sharing"), ("TelnetClient", "Telnet client"),
                 ("TFTP", "TFTP client"), ("IIS-WebServerRole", "IIS web server")]
        for fname, label in feats:
            rc, out = ps("(Get-WindowsOptionalFeature -Online -FeatureName %s).State" % fname, show=False)
            if "Enabled" in out:
                if yes_no("  %s is turned on - disable it?" % label):
                    ps("Disable-WindowsOptionalFeature -Online -FeatureName %s -NoRestart" % fname)
                    log("disabled feature " + label)
                    note("disabled-features.txt", fname)
            else:
                print("  %s is already off" % label)

    # ---------- 11. services ----------
    if step(11, "check for services you might not need, one at a time?"):
        svcs = ["RemoteRegistry", "TlntSvr", "SNMP", "SSDPSRV", "upnphost", "FTPSVC",
                "W3SVC", "RemoteAccess", "Browser", "lltdsvc", "Fax", "simptcp",
                "SharedAccess", "WinRM", "LanmanServer", "TermService"]
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
    if step(12, "deal with remote desktop and remote assistance?"):
        ts = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"
        if yes_no("  does the readme say remote desktop is REQUIRED?"):
            reg_set(ts + r"\WinStations\RDP-Tcp", "UserAuthentication", 1)
            log("RDP kept, network level authentication required")
        else:
            reg_set(ts, "fDenyTSConnections", 1)
            run('netsh advfirewall firewall set rule group="remote desktop" new enable=No', show=False)
            log("remote desktop disabled")
        reg_set(r"HKLM\SYSTEM\CurrentControlSet\Control\Remote Assistance", "fAllowToGetHelp", 0)
        log("remote assistance off")

    # ---------- 13. security settings ----------
    if step(13, "go through the security registry settings one at a time?"):
        pol = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        lsa = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
        settings = [
            ("turn UAC on", pol, "EnableLUA", 1),
            ("UAC: always ask admins for consent", pol, "ConsentPromptBehaviorAdmin", 2),
            ("require ctrl+alt+del to log in", pol, "DisableCAD", 0),
            ("don't show the last username at login", pol, "DontDisplayLastUserName", 1),
            ("lock the screen after 15 minutes", pol, "InactivityTimeoutSecs", 900),
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
    if step(14, "scan for media files, open ports, startup items, tasks (report only)?"):
        exts = (".mp3", ".mp4", ".avi", ".mkv", ".wav", ".flac", ".mov", ".ogg", ".wma", ".wmv")
        log("--- media files in C:\\Users ---")
        for root, dirs, files in os.walk("C:\\Users"):
            dirs[:] = [d for d in dirs if d.lower() != "appdata"]
            for fn in files:
                if fn.lower().endswith(exts):
                    log(os.path.join(root, fn))

        log("--- listening ports ---")
        rc, out = ps("Get-NetTCPConnection -State Listen | Sort-Object LocalPort | ForEach-Object { "
                     "'' + $_.LocalAddress + ':' + $_.LocalPort + '   ' + "
                     "(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName }", show=False)
        log(out)

        log("--- scheduled tasks (non microsoft) ---")
        rc, out = ps("Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft*' } | "
                     "Select-Object TaskName,State | Format-Table -AutoSize | Out-String -Width 200", show=False)
        log(out)

        log("--- startup programs ---")
        rc, out = ps("Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,User | "
                     "Format-Table -AutoSize | Out-String -Width 200", show=False)
        log(out)

        log("--- network shares ---")
        rc, out = run("net share", show=False)
        log(out)

        log("--- hosts file (non comment lines) ---")
        try:
            with open(r"C:\Windows\System32\drivers\etc\hosts") as f:
                for line in f:
                    if line.strip() and not line.strip().startswith("#"):
                        log(line.strip())
        except Exception as e:
            log("couldn't read hosts: %s" % e)

    print("\nall done. report is at " + LOG)
    print("still todo by hand: forensics questions, readme users/admins, media files,")
    print("browser settings, then reboot.")
    input("press enter to close")


if __name__ == "__main__":
    main()
