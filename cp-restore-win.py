# cp-restore-win.py - puts back the backup made by cp-win.py or cp-server.py
# works for windows 11 and windows server. run it as administrator.
# to turn it into an exe:
#   pip install pyinstaller
#   pyinstaller --onefile --uac-admin cp-restore-win.py

import csv
import ctypes
import os
import subprocess
import sys

BACKUP = r"C:\cp-backup"
HOME = os.path.expanduser("~")
DESK = os.path.join(HOME, "Desktop")
LOG = os.path.join(DESK if os.path.isdir(DESK) else HOME, "cp-restore-report.txt")

LABEL = {"server": "Windows Server", "windows": "Windows (client, e.g. Windows 11)"}

# the registry keys the hardening scripts exported (reg0.reg ... reg4.reg, same order)
REG_FILES = [
    r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies",
    r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate",
    r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa",
    r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server",
    r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters",
]

# registry values the hardening scripts set. if one of these wasn't in the
# backup, it didn't exist before, so restoring means deleting it
_pol = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
_lsa = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
_ts = r"HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"
MANAGED = [
    (_pol, "EnableLUA"), (_pol, "ConsentPromptBehaviorAdmin"), (_pol, "DisableCAD"),
    (_pol, "DontDisplayLastUserName"), (_pol, "InactivityTimeoutSecs"),
    (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer", "NoDriveTypeAutoRun"),
    (_lsa, "RestrictAnonymous"), (_lsa, "RestrictAnonymousSAM"),
    (_lsa, "LimitBlankPasswordUse"), (_lsa, "NoLMHash"),
    (r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters", "SMB1"),
    (r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU", "NoAutoUpdate"),
    (r"HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU", "AUOptions"),
    (_ts, "fDenyTSConnections"),
    (_ts + r"\WinStations\RDP-Tcp", "UserAuthentication"),
]

START_MAP = {"automatic": "auto", "manual": "demand", "disabled": "disabled"}


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
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                       text=True, errors="replace")
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if show and out:
        print(out)
    return r.returncode, out


def ps(cmd, show=True):
    return run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", cmd], show)


def path(name):
    return os.path.join(BACKUP, name)


def read_list(filename):
    # one item per line, no duplicates, keeps the order
    p = path(filename)
    items = []
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and line not in items:
                    items.append(line)
    return items


# ---------- figuring out what kind of backup this is ----------

def read_info():
    info = {}
    p = path("backup-info.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    info[k] = v
    return info


def backup_kind(info):
    if info.get("type") in ("server", "windows"):
        return info["type"], "from the backup info file"
    # older backups don't have the info file, so guess from the other files
    if os.path.exists(path("removed-features.txt")):
        return "server", "guessed from removed-features.txt"
    if os.path.exists(path("disabled-features.txt")):
        return "windows", "guessed from disabled-features.txt"
    return None, "no way to tell"


def current_os():
    rc, out = ps("$o = Get-CimInstance Win32_OperatingSystem; '' + $o.ProductType + '|' + $o.Caption",
                 show=False)
    last = out.strip().splitlines()[-1] if out.strip() else ""
    ptype, _, caption = last.partition("|")
    return ("server" if ptype in ("2", "3") else "windows"), caption.strip()


# ---------- registry helpers ----------

def norm_key(k):
    return k.strip().lower().replace("hkey_local_machine", "hklm").rstrip("\\")


def parse_reg(p):
    # returns a set of (key, value name) that exist in a .reg export
    vals = set()
    section = ""
    try:
        with open(p, encoding="utf-16") as f:
            lines = f.read().splitlines()
    except Exception:
        with open(p, encoding="cp1252", errors="replace") as f:
            lines = f.read().splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = norm_key(line[1:-1])
        elif line.startswith('"') and '"=' in line:
            vals.add((section, line[1:line.index('"=', 1)].lower()))
    return vals


# ---------- service helpers ----------

def read_services_csv():
    rows = {}
    p = path("services.csv")
    if os.path.exists(p):
        with open(p, newline="", encoding="utf-8-sig", errors="replace") as f:
            for row in csv.DictReader(f):
                if row.get("Name"):
                    rows[row["Name"].lower()] = row
    return rows


def current_start(name):
    rc, out = run(["sc", "qc", name], show=False)
    if rc != 0:
        return None
    if "DISABLED" in out:
        return "disabled"
    if "AUTO_START" in out:
        return "auto"
    if "DEMAND_START" in out:
        return "demand"
    return None


# ---------- admin group helpers ----------

def admins():
    rc, out = ps("Get-LocalGroupMember -SID S-1-5-32-544 | "
                 "ForEach-Object { $_.Name.Split('\\')[-1] }", show=False)
    return [l.strip() for l in out.splitlines() if l.strip()]


def original_admins():
    p = path("admins.txt")
    names, started = [], False
    if not os.path.exists(p):
        return names
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("---"):
                started = True
                continue
            if not started or not line:
                continue
            if line.lower().startswith("the command completed"):
                break
            names.append(line)
    return names


def main():
    if not is_admin():
        print("run this as administrator (right click > run as administrator)")
        input("press enter to close")
        sys.exit(1)

    with open(LOG, "w", encoding="utf-8") as f:
        f.write("cp restore run\n")

    print("=== CyberPatriot backup restore (Windows 11 / Windows Server) ===\n")

    if not os.path.exists(path(".done")):
        print("no backup found in %s" % BACKUP)
        print("did cp-win or cp-server run on this machine first?")
        input("press enter to close")
        sys.exit(1)

    # ---------- what kind of backup is this? ----------
    info = read_info()
    kind, how = backup_kind(info)
    now_kind, now_caption = current_os()

    print("backup folder : " + BACKUP)
    if kind:
        taken = info.get("os", "")
        print("backup type   : %s%s  (%s)" % (LABEL[kind],
              "  [taken on: %s]" % taken if taken else "", how))
    else:
        kind = now_kind
        print("backup type   : couldn't tell, assuming it matches this machine")
    print("this machine  : %s  (%s)" % (now_caption or "unknown", LABEL[now_kind]))

    print("\nwhat's in the backup:")
    names = [("firewall.wfw", "firewall rules and settings"),
             ("secpol-original.inf", "local security policy (password/lockout rules)"),
             ("services.csv", "service start types"),
             ("admins.txt", "who was in the Administrators group"),
             ("disabled-services.txt", "services the script disabled"),
             ("removed-features.txt", "server roles/features the script removed"),
             ("disabled-features.txt", "windows features the script turned off"),
             ("removed-programs.txt", "programs the script uninstalled")]
    for fn, desc in names:
        print("  [%s] %s" % ("x" if os.path.exists(path(fn)) else " ", desc))
    regs = [i for i in range(len(REG_FILES)) if os.path.exists(path("reg%d.reg" % i))]
    print("  [%s] registry exports (%d of %d)" % ("x" if regs else " ", len(regs), len(REG_FILES)))

    if kind != now_kind:
        print("\nWARNING: this is a %s backup but this machine is %s." % (LABEL[kind], LABEL[now_kind]))
        print("restoring it here could put the wrong settings on this machine.")
        if not yes_no("continue anyway?"):
            print("stopped, nothing was changed")
            input("press enter to close")
            sys.exit(0)

    # ---------- the main question ----------
    print()
    print("restoring puts the machine's settings back the way they were before the")
    print("hardening script ran. you'll still be asked about each part first.")
    if not yes_no("Is it okay to restore this %s backup?" % LABEL[kind]):
        print("ok, nothing was changed")
        input("press enter to close")
        sys.exit(0)

    rc, out = ps("(Get-CimInstance Win32_ComputerSystem).DomainRole", show=False)
    is_dc = out.strip() in ("4", "5")

    # ---------- 1. firewall ----------
    if step(1, "restore the original firewall rules and settings?"):
        if os.path.exists(path("firewall.wfw")):
            rc, out = run(["netsh", "advfirewall", "import", path("firewall.wfw")])
            log("firewall restored" if rc == 0 else "firewall restore FAILED")
        else:
            print("no firewall backup, skipping")

    # ---------- 2. security policy ----------
    if step(2, "restore the original password/lockout/security policy?"):
        if os.path.exists(path("secpol-original.inf")):
            rc, out = run(["secedit", "/configure", "/db", path("secpol-restore.sdb"),
                           "/cfg", path("secpol-original.inf"), "/areas", "SECURITYPOLICY"])
            log("security policy restored" if rc == 0 else "security policy restore FAILED")
        else:
            print("no security policy backup, skipping")

    # ---------- 3. registry ----------
    if step(3, "restore the registry settings (UAC, autorun, anonymous access, SMB1, RDP, update policy)?"):
        originals = set()
        for i, k in enumerate(REG_FILES):
            p = path("reg%d.reg" % i)
            if os.path.exists(p):
                rc, out = run(["reg", "import", p], show=False)
                originals |= parse_reg(p)
                log("imported %s" % k if rc == 0 else "import had problems: %s" % k)
            else:
                print("no export for %s (it probably didn't exist back then)" % k)
        # values the scripts added that weren't there originally
        for key, name in MANAGED:
            covering = [i for i, ek in enumerate(REG_FILES)
                        if key.lower() == ek.lower() or key.lower().startswith(ek.lower() + "\\")]
            if not covering:
                continue
            if (norm_key(key), name.lower()) in originals:
                continue
            rc, out = run(["reg", "delete", key, "/v", name, "/f"], show=False)
            if rc == 0:
                log("removed added value %s\\%s" % (key, name))
        print("remote assistance and the defender policy change weren't backed up, so those stay as they are")

    # ---------- 4. services ----------
    if step(4, "put services back the way they were?"):
        rows = read_services_csv()
        todo = read_list("disabled-services.txt")
        if not todo:
            print("the script didn't disable any services")
        for name in todo:
            orig = rows.get(name.lower())
            start = START_MAP.get((orig or {}).get("StartType", "").lower())
            if not start:
                print("  no saved start type for %s, skipping" % name)
                continue
            if yes_no("  restore service '%s' to %s?" % (name, orig["StartType"])):
                run(["sc", "config", name, "start=", start])
                if orig.get("Status", "").lower() == "running" and start != "disabled":
                    run(["sc", "start", name], show=False)
                log("restored service %s to %s" % (name, orig["StartType"]))
        # the update step switched these two to automatic
        for name in ("wuauserv", "bits"):
            orig = rows.get(name)
            start = START_MAP.get((orig or {}).get("StartType", "").lower())
            cur = current_start(name)
            if start and cur and cur != start:
                if yes_no("  restore service '%s' to %s? (the update step changed it)" % (name, orig["StartType"])):
                    run(["sc", "config", name, "start=", start])
                    log("restored service %s to %s" % (name, orig["StartType"]))

    # ---------- 5. roles and features ----------
    if step(5, "bring back removed server roles / windows features, one at a time?"):
        any_found = False
        for fname in read_list("removed-features.txt"):
            any_found = True
            if yes_no("  reinstall server role/feature '%s'?" % fname):
                ps("Install-WindowsFeature -Name %s" % fname)
                log("reinstalled feature " + fname)
        for fname in read_list("disabled-features.txt"):
            any_found = True
            if yes_no("  turn windows feature '%s' back on?" % fname):
                ps("Enable-WindowsOptionalFeature -Online -FeatureName %s -NoRestart" % fname)
                log("re-enabled feature " + fname)
        if not any_found:
            print("the script didn't remove any features")
        else:
            print("features may need a reboot to finish")

    # ---------- 6. administrators group ----------
    if is_dc:
        print("\nStep 6 skipped (domain controller), use Active Directory Users and Computers")
    elif step(6, "put people back in the Administrators group if the script took them out?"):
        now = [a.lower() for a in admins()]
        original = original_admins()
        if not original:
            print("no saved admin list")
        for member in original:
            short = member.split("\\")[-1]
            if short.lower() in now:
                continue
            rc, out = run(["net", "user", short], show=False)
            if rc != 0:
                print("  %s isn't a local user anymore (deleted?), can't add back" % short)
                continue
            if yes_no("  %s was an administrator, add back?" % short):
                run(["net", "localgroup", "Administrators", short, "/add"])
                log("added %s back to administrators" % short)
        if yes_no("  enable the built-in Administrator account again? (the backup doesn't record if it was on)"):
            run(["net", "user", "Administrator", "/active:yes"])
            log("built-in Administrator enabled")

    # ---------- 7. removed programs (info only) ----------
    removed = read_list("removed-programs.txt")
    print()
    if removed:
        print("programs the script uninstalled (reinstall these yourself if you need them):")
        for r in removed:
            print("  - " + r)
    else:
        print("the script didn't uninstall any programs")

    print("\nrestore finished. reboot so everything takes effect.")
    print("can't be undone by this script: windows updates that were installed,")
    print("deleted users, passwords it changed, removed defender exclusions,")
    print("and the remote assistance / defender policy tweaks.")
    print("report saved to " + LOG)
    input("press enter to close")


if __name__ == "__main__":
    main()
