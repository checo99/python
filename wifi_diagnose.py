#!/usr/bin/env python3
"""WiFi slowdown diagnostic. Run on the computer with the slow WiFi:

    python3 wifi_diagnose.py

Works on macOS, Windows and Linux. Uses only the standard library and the
operating system's own tools. It only reads information and changes nothing.
"""
import platform
import re
import shutil
import socket
import statistics
import subprocess
import time

OS = platform.system()
findings = []


def run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              shell=isinstance(cmd, str)).stdout
    except Exception:
        return ""


def header(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- WiFi link
def wifi_link():
    header("WiFi link (signal, noise, channel, speed)")
    rssi = noise = None
    if OS == "Darwin":
        out = run(["system_profiler", "SPAirPortDataType"], timeout=60)
        cur = out.split("Current Network Information:")
        block = cur[1].split("Other Local Wi-Fi Networks")[0] if len(cur) > 1 else ""
        for line in block.splitlines()[:14]:
            if line.strip():
                print(line.rstrip())
        m = re.search(r"Signal / Noise:\s*(-?\d+) dBm / (-?\d+) dBm", block)
        if m:
            rssi, noise = int(m.group(1)), int(m.group(2))
        ch = re.search(r"Channel:\s*(\d+)", block)
        if ch and int(ch.group(1)) <= 14:
            findings.append("You are on the 2.4 GHz band (channel "
                            f"{ch.group(1)}). It's slower and more crowded. Use the 5 GHz "
                            "network if your router has one.")
        others = out.split("Other Local Wi-Fi Networks")
        if len(others) > 1 and ch:
            same = len(re.findall(rf"Channel:\s*{ch.group(1)}\b", others[1]))
            print(f"Other networks on your channel: {same}")
            if same >= 4:
                findings.append(f"{same} other networks share your channel "
                                f"{ch.group(1)}. Change the router's channel, or set it "
                                "to auto.")
    elif OS == "Windows":
        out = run("netsh wlan show interfaces")
        print(out.strip())
        m = re.search(r"Signal\s*:\s*(\d+)%", out)
        if m:
            pct = int(m.group(1))
            rssi = pct / 2 - 100  # rough conversion
        ch = re.search(r"Channel\s*:\s*(\d+)", out)
        if ch and int(ch.group(1)) <= 14:
            findings.append("You are on the 2.4 GHz band. Use the 5 GHz network if "
                            "your router has one.")
    else:
        if shutil.which("nmcli"):
            print(run("nmcli -f IN-USE,SSID,CHAN,RATE,SIGNAL dev wifi | head -15"))
        iw = shutil.which("iw")
        if iw:
            for dev in re.findall(r"Interface (\S+)", run([iw, "dev"])):
                out = run([iw, "dev", dev, "link"])
                print(out.strip())
                m = re.search(r"signal:\s*(-?\d+)", out)
                if m:
                    rssi = int(m.group(1))
    if rssi is not None:
        print(f"Signal strength: {rssi:.0f} dBm")
        if rssi < -75:
            findings.append(f"The signal is weak ({rssi:.0f} dBm). Move closer to the "
                            "router, remove obstacles, or add a mesh node or extender.")
        elif rssi < -67:
            findings.append(f"The signal is fair ({rssi:.0f} dBm). Speed will be limited, "
                            "especially for video calls and streaming.")
    if rssi is not None and noise is not None and rssi - noise < 20:
        findings.append(f"The signal-to-noise ratio is low ({rssi - noise} dB). There's "
                        "interference, often from microwaves, Bluetooth or neighbors' "
                        "networks.")


# ------------------------------------------------------------------ latency
def gateway():
    if OS == "Darwin":
        m = re.search(r"gateway:\s*(\S+)", run("route -n get default"))
    elif OS == "Windows":
        m = re.search(r"0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)", run("route print -4"))
    else:
        m = re.search(r"default via (\S+)", run("ip route"))
    return m.group(1) if m else None


def ping(host, count=20):
    flag = "-n" if OS == "Windows" else "-c"
    out = run(["ping", flag, str(count), host], timeout=count * 3 + 10)
    times = [float(t) for t in re.findall(r"time[=<]([\d.]+)", out)]
    loss = 100 * (count - len(times)) / count
    return times, loss


def latency():
    header("Latency and packet loss")
    targets = [("router", gateway()), ("internet (1.1.1.1)", "1.1.1.1")]
    res = {}
    for name, host in targets:
        if not host:
            continue
        times, loss = ping(host)
        if times:
            avg, jit = statistics.mean(times), statistics.pstdev(times)
            print(f"{name:20} avg {avg:6.1f} ms  jitter {jit:5.1f} ms  "
                  f"max {max(times):6.1f} ms  loss {loss:.0f}%")
        else:
            avg, jit = None, None
            print(f"{name:20} no replies")
        res[name] = (avg, jit, loss)
    r = res.get("router")
    if r and r[0] is not None:
        if r[0] > 10 or r[1] > 10 or r[2] > 0:
            findings.append("The link from your device to the router is unstable "
                            f"(avg {r[0]:.0f} ms, jitter {r[1]:.0f} ms, loss {r[2]:.0f}%). "
                            "The problem is the WiFi itself, not your internet provider.")
        else:
            i = res.get("internet (1.1.1.1)")
            if i and i[0] is not None and (i[0] > 60 or i[2] > 1):
                findings.append("The WiFi to the router is fine, but the internet is "
                                "slow past the router. The cause is your modem or ISP, or "
                                "another device saturating the connection. Restart the "
                                "modem and router, and check other devices.")


# ---------------------------------------------------------------------- DNS
def dns():
    header("DNS lookup time")
    slow = []
    for host in ["google.com", "apple.com", "wikipedia.org", "netflix.com"]:
        t = time.perf_counter()
        try:
            socket.getaddrinfo(host, 443)
            ms = (time.perf_counter() - t) * 1000
            print(f"{host:16} {ms:6.0f} ms")
            if ms > 150:
                slow.append(host)
        except Exception:
            print(f"{host:16} FAILED")
            slow.append(host)
    if len(slow) >= 2:
        findings.append("DNS lookups are slow, so every new page waits. Set your DNS to "
                        "1.1.1.1 or 8.8.8.8.")


# --------------------------------------------------------- bandwidth hogs
def hogs():
    header("Processes using the network")
    if OS == "Darwin":
        out = run("nettop -P -L 1 -x -J bytes_in,bytes_out", timeout=20)
        rows = []
        for line in out.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 3:
                try:
                    rows.append((int(parts[1] or 0) + int(parts[2] or 0), parts[0]))
                except ValueError:
                    pass
        rows.sort(reverse=True)
        print("Total bytes since each process started (top 10):")
        for b, name in rows[:10]:
            print(f"  {b / 1e6:10.1f} MB  {name}")
        sync = [n for _, n in rows[:10] if re.search(
            r"cloudd|bird|nsurlsessiond|softwareupdate|Dropbox|OneDrive|Google Drive|"
            r"photoanalysisd|backupd|Steam|torrent", n, re.I)]
        if sync:
            findings.append("These sync, backup or update processes have used a lot of "
                            f"data: {', '.join(sync)}. Pause them and test again.")
    elif OS == "Windows":
        print(run('powershell -NoProfile -Command "Get-NetTCPConnection -State Established '
                  '| Group-Object OwningProcess | Sort-Object Count -Descending | '
                  'Select-Object -First 10 Count,@{n=\'Process\';e={(Get-Process -Id '
                  '$_.Name -ErrorAction SilentlyContinue).ProcessName}} | Format-Table"'))
        print("For live per-app usage: Task Manager > Performance > Open Resource "
              "Monitor > Network tab.")
    else:
        print(run("ss -tnp state established 2>/dev/null | awk 'NR>1{print $NF}' | "
                  "grep -o '\"[^\"]*\"' | sort | uniq -c | sort -rn | head -10"))
        print("For live per-app usage, run: sudo nethogs")


def main():
    print(f"WiFi diagnostic on {OS} {platform.release()}")
    for step in (wifi_link, latency, dns, hogs):
        try:
            step()
        except Exception as e:
            print(f"  (skipped: {e})")
    header("FINDINGS")
    if findings:
        for i, f in enumerate(findings, 1):
            print(f"{i}. {f}")
    else:
        print("Nothing obviously wrong on this device. Run a speed test at "
              "speed.cloudflare.com and compare it to your plan. If it's slow on every "
              "device, restart the modem and router or contact your ISP.")


if __name__ == "__main__":
    main()
