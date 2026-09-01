"""
WiFi USB Monitor - Monitor de Adaptador WiFi USB para Windows 11
Monitoriza: conexión a internet, estado del adaptador USB, señal, latencia y causas de desconexión.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import time
import datetime
import json
import os
import re
import socket
import platform
import sys
import ctypes
import queue
import winsound
from collections import deque

# ─── Verificar Windows ───────────────────────────────────────────────────────
if platform.system() != "Windows":
    print("Este programa solo funciona en Windows.")
    sys.exit(1)

try:
    import winreg
except ImportError:
    pass

# ─── Constantes ──────────────────────────────────────────────────────────────
VERSION = "1.1.0"
LOG_FILE = os.path.join(os.path.expanduser("~"), "Desktop", "wifi_monitor_log.txt")
PING_HOSTS = ["8.8.8.8", "1.1.1.1", "9.9.9.9"]
CHECK_INTERVAL = 3  # segundos
HISTORY_SIZE = 100
LATENCY_HISTORY = 60  # puntos en el gráfico
SPEED_HISTORY   = 60  # puntos en el gráfico de velocidad

# Colores - Tema Dark Industrial
C = {
    "bg":        "#0d1117",
    "bg2":       "#161b22",
    "bg3":       "#21262d",
    "border":    "#30363d",
    "green":     "#3fb950",
    "green_dim": "#1f4d2a",
    "red":       "#f85149",
    "red_dim":   "#4d1f1f",
    "yellow":    "#e3b341",
    "blue":      "#58a6ff",
    "purple":    "#bc8cff",
    "text":      "#e6edf3",
    "text_dim":  "#8b949e",
    "accent":    "#1f6feb",
    "orange":    "#d29922",
    "cyan":      "#39d0d8",
    "cyan_dim":  "#0d3d40",
    "pink":      "#ff7b72",
    "pink_dim":  "#4d1f1f",
}

# ─── Utilidades de Sistema ────────────────────────────────────────────────────

def run_cmd(cmd, timeout=10):
    """Ejecuta comando y devuelve salida."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=timeout,
            encoding='utf-8', errors='replace'
        )
        return result.stdout.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", -1
    except Exception as e:
        return str(e), -1


def ping(host, timeout=2):
    """Hace ping y devuelve latencia en ms o None."""
    try:
        out, code = run_cmd(f"ping -n 1 -w {timeout*1000} {host}", timeout=timeout+2)
        if code == 0 and "tiempo=" in out.lower() or "time=" in out.lower():
            match = re.search(r"[Tt]iempo[<=](\d+)ms|[Tt]ime[<=](\d+)ms", out)
            if match:
                return int(match.group(1) or match.group(2))
            return 1  # < 1ms
        return None
    except Exception:
        return None


def run_ps(script, timeout=12):
    """Ejecuta PowerShell y devuelve salida."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace'
        )
        return result.stdout.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", -1
    except Exception as e:
        return str(e), -1


def detect_usb_adapter():
    """Detecta adaptadores WiFi USB usando PowerShell (compatible con Windows 11)."""
    # Método 1: PowerShell Get-NetAdapter + PnPDevice para buscar USB
    ps = (
        "Get-NetAdapter | Where-Object { $_.InterfaceDescription -match 'wireless|wifi|wlan|802\\.11|usb.*net|net.*usb|ralink|realtek.*wireless|mediatek|tp-link|edimax|alfa|comfast|asus.*usb|netgear.*usb' } | "
        "Select-Object Name, InterfaceDescription, MacAddress, Status, LinkSpeed | "
        "ConvertTo-Json -Compress"
    )
    out, code = run_ps(ps, timeout=10)
    adapters = []

    if out and out.strip().startswith(("[", "{")):
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                adapters.append({
                    "Name": item.get("Name", ""),
                    "Description": item.get("InterfaceDescription", ""),
                    "MACAddress": item.get("MacAddress", ""),
                    "Status": item.get("Status", ""),
                    "Speed": item.get("LinkSpeed", ""),
                    "source": "netadapter",
                })
        except Exception:
            pass

    # Método 2: Buscar específicamente por PNPDeviceID que contenga USB
    if not adapters:
        ps2 = (
            "Get-WmiObject Win32_NetworkAdapter | "
            "Where-Object { $_.PNPDeviceID -like 'USB*' -and $_.Name -ne $null } | "
            "Select-Object Name, MACAddress, PNPDeviceID, NetConnectionStatus | "
            "ConvertTo-Json -Compress"
        )
        out2, _ = run_ps(ps2, timeout=10)
        if out2 and out2.strip().startswith(("[", "{")):
            try:
                data2 = json.loads(out2)
                if isinstance(data2, dict):
                    data2 = [data2]
                for item in data2:
                    adapters.append({
                        "Name": item.get("Name", ""),
                        "MACAddress": item.get("MACAddress", ""),
                        "PNPDeviceID": item.get("PNPDeviceID", ""),
                        "source": "wmi_usb",
                    })
            except Exception:
                pass

    # Método 3: Fallback — cualquier adaptador WiFi activo detectado por netsh
    if not adapters:
        raw, _ = run_cmd("netsh wlan show interfaces")
        if raw and ("conectado" in raw.lower() or "connected" in raw.lower() or
                    "autenticando" in raw.lower() or "authenticating" in raw.lower() or
                    len(raw) > 100):
            # Extraer nombre del adaptador
            name_match = re.search(r"[Nn]ombre\s*:\s*(.+)|[Nn]ame\s*:\s*(.+)", raw)
            desc_match = re.search(r"[Dd]escripci[oó]n\s*:\s*(.+)|[Dd]escription\s*:\s*(.+)", raw)
            mac_match  = re.search(r"[Dd]irecci[oó]n f[ií]sica\s*:\s*(.+)|[Pp]hysical address\s*:\s*(.+)", raw)
            adapters.append({
                "Name": (name_match.group(1) or name_match.group(2)).strip() if name_match else "Adaptador WiFi",
                "Description": (desc_match.group(1) or desc_match.group(2)).strip() if desc_match else "",
                "MACAddress": (mac_match.group(1) or mac_match.group(2)).strip() if mac_match else "",
                "source": "netsh_fallback",
            })

    return adapters





def get_dns_status():
    """Verifica resolución DNS."""
    try:
        start = time.time()
        socket.getaddrinfo("google.com", 80, timeout=3)
        return True, int((time.time() - start) * 1000)
    except Exception:
        return False, None


def get_signal_quality(raw_interfaces):
    """Compatibilidad — ahora delega al parser unificado."""
    return parse_netsh_interfaces(raw_interfaces).get("signal")


def parse_netsh_interfaces(raw):
    """
    Parsea 'netsh wlan show interfaces' de forma robusta.
    Funciona con Windows en español, inglés y cualquier codificación.
    Devuelve dict con claves normalizadas.
    """
    data = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        # Partir solo por el primer ':'
        key, _, val = line.partition(":")
        k = key.strip().lower()
        v = val.strip()
        if not k or not v:
            continue
        data[k] = v

    def get(*keys):
        for k in keys:
            # Búsqueda exacta
            if k in data:
                return data[k]
            # Búsqueda parcial (el key contiene la palabra)
            for dk, dv in data.items():
                if k in dk:
                    return dv
        return None

    result = {}

    # Estado
    estado = get("estado", "state")
    result["connected"] = bool(estado and ("conectado" in estado.lower() or
                                            "connected" in estado.lower()))

    # SSID (evitar BSSID)
    for dk, dv in data.items():
        if "ssid" in dk and "bssid" not in dk:
            result["ssid"] = dv
            break

    # BSSID
    result["bssid"] = get("bssid")

    # Señal / Signal
    sig_raw = get("calidad de señal", "calidad de se", "signal", "señal")
    if sig_raw:
        m = re.search(r"(\d+)", sig_raw)
        result["signal"] = int(m.group(1)) if m else None
    else:
        result["signal"] = None

    # Canal
    canal = get("canal", "channel")
    if canal:
        m = re.search(r"(\d+)", canal)
        result["channel"] = int(m.group(1)) if m else None

    # Tipo de radio
    result["radio_type"] = get("tipo de radio", "radio type")

    # Velocidades TX / RX — netsh en español usa variantes distintas
    # Buscamos en todas las claves que contengan "transmis" o "tx" o "envío"
    for dk, dv in data.items():
        if any(x in dk for x in ["transmis", "tx ", "envío", "send", "transmit"]):
            result["tx_rate"] = dv
            break
    for dk, dv in data.items():
        if any(x in dk for x in ["recep", "rx ", "receive", "recib"]):
            result["rx_rate"] = dv
            break

    # Fallback TX/RX: buscar "mbps" en líneas con números
    if not result.get("tx_rate") or not result.get("rx_rate"):
        rate_lines = [(k, v) for k, v in data.items() if "mbps" in v.lower() or re.search(r"\d+[.,]\d+\s*mbps", v, re.I)]
        for i, (k, v) in enumerate(rate_lines):
            if not result.get("tx_rate"):
                result["tx_rate"] = v
            elif not result.get("rx_rate"):
                result["rx_rate"] = v

    # MAC física del adaptador
    for dk, dv in data.items():
        if any(x in dk for x in ["física", "fisic", "physical", "mac"]):
            if re.search(r"[0-9a-f]{2}[:\-]", dv, re.I):
                result["mac"] = dv
                break

    # Nombre del adaptador (primera línea con "nombre" o "name")
    result["adapter_name"] = get("nombre", "name")

    return result


def get_ip_info():
    """Obtiene IP local y gateway usando PowerShell (más fiable que ipconfig regex)."""
    ps = (
        "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null } | "
        "Select-Object -First 1 | "
        "Select-Object @{N='IP';E={$_.IPv4Address.IPAddress}}, "
        "@{N='GW';E={$_.IPv4DefaultGateway.NextHop}} | "
        "ConvertTo-Json -Compress"
    )
    out, code = run_ps(ps, timeout=8)
    if out and "{" in out:
        try:
            d = json.loads(out)
            return d.get("IP"), d.get("GW")
        except Exception:
            pass

    # Fallback: ipconfig con regex más amplio
    raw, _ = run_cmd("ipconfig")
    ip, gw = None, None

    # Buscar sección WiFi/WLAN y extraer IP y GW de ahí
    sections = re.split(r"\n\s*\n", raw)
    for sec in sections:
        if any(k in sec.lower() for k in ["wi-fi", "wifi", "wlan", "wireless", "inalámbr"]):
            ip_m = re.search(r"IPv4.*?:\s*([\d]{1,3}\.[\d]{1,3}\.[\d]{1,3}\.[\d]{1,3})", sec)
            gw_m = re.search(r"[Gg]ateway.*?:\s*([\d]{1,3}\.[\d]{1,3}\.[\d]{1,3}\.[\d]{1,3})|"
                              r"[Ee]nlace.*?:\s*([\d]{1,3}\.[\d]{1,3}\.[\d]{1,3}\.[\d]{1,3})", sec)
            if ip_m:
                ip = ip_m.group(1)
            if gw_m:
                gw = gw_m.group(1) or gw_m.group(2)
            if ip:
                break

    # Si no encontró en sección WiFi, buscar en todo el output
    if not ip:
        ip_m = re.search(r"IPv4.*?:\s*([\d]{1,3}\.[\d]{1,3}\.[\d]{1,3}\.[\d]{1,3})", raw)
        gw_m = re.search(r"(?:[Gg]ateway|[Ee]nlace).*?:\s*([\d]{1,3}\.[\d]{1,3}\.[\d]{1,3}\.[\d]{1,3})", raw)
        if ip_m:
            ip = ip_m.group(1)
        if gw_m:
            gw = gw_m.group(1)

    return ip, gw



# ─── Medición de velocidad de red (hilo dedicado, ~1 s) ──────────────────────

class SpeedMeter:
    """
    Lee bytes RX/TX del adaptador WiFi usando 'netsh interface ipv4 show
    interfaces' o el registro de rendimiento de Windows (typeperf).
    Corre en su propio hilo a 1 segundo de intervalo para máxima precisión.
    """
    INTERVAL = 1.0   # segundos entre muestras

    def __init__(self, speed_callback):
        """
        speed_callback(down_mbps, up_mbps) se llama cada vez que hay datos.
        """
        self._cb  = speed_callback
        self._thr = None
        self._running = False
        self._lock  = threading.Lock()
        self._last  = (None, None)   # último (down, up) válido
        self._history = deque(maxlen=SPEED_HISTORY)

        # Detectar qué método usar
        self._method = self._detect_method()

    # ── Detección de método ──────────────────────────────────────────────────

    def _detect_method(self):
        """Intenta cada método y devuelve el primero que funcione."""
        if self._try_netsh_bytes() is not None:
            return "netsh"
        if self._try_wmi_bytes() is not None:
            return "wmi"
        return "netstat"

    def _try_netsh_bytes(self):
        """netsh interface ipv4 show interfaces — devuelve (rx, tx) o None."""
        out, code = run_cmd("netsh interface ipv4 show interfaces", timeout=4)
        if code == 0 and out:
            return self._parse_netsh_if(out)
        return None

    def _parse_netsh_if(self, out):
        """Suma bytes de todas las interfaces WiFi en el output de netsh."""
        # El output tiene columnas: Idx  Met  MTU  State  Name
        # No incluye bytes; usamos el registro de rendimiento en su lugar.
        return None  # netsh if no da bytes — usar registry

    def _try_wmi_bytes(self):
        """Usa Win32_PerfRawData_Tcpip_NetworkInterface via PowerShell."""
        ps = (
            "Get-WmiObject Win32_PerfRawData_Tcpip_NetworkInterface | "
            "Select-Object BytesReceivedPersec, BytesSentPersec, Name | "
            "ConvertTo-Json -Compress"
        )
        out, _ = run_ps(ps, timeout=6)
        if out and ("{" in out or "[" in out):
            try:
                data = json.loads(out)
                if isinstance(data, dict):
                    data = [data]
                # Buscar adaptador WiFi/wireless
                for d in data:
                    name = str(d.get("Name", "")).lower()
                    if any(k in name for k in ["wi-fi", "wifi", "wlan", "wireless", "802"]):
                        rx = int(d.get("BytesReceivedPersec") or 0)
                        tx = int(d.get("BytesSentPersec") or 0)
                        return rx, tx
                # Fallback: primer adaptador con tráfico
                for d in data:
                    rx = int(d.get("BytesReceivedPersec") or 0)
                    tx = int(d.get("BytesSentPersec") or 0)
                    if rx > 0 or tx > 0:
                        return rx, tx
            except Exception:
                pass
        return None

    # ── Métodos de lectura de bytes acumulados ───────────────────────────────

    def _read_bytes_registry(self):
        """
        Lee contadores de red desde el registro de rendimiento de Windows.
        Es la fuente más rápida y precisa — la misma que usa el Administrador
        de tareas. No requiere PowerShell.
        """
        try:
            import winreg
            # Obtener índice del objeto "Network Interface"
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Perflib\CurrentLanguage")
            counter_data, _ = winreg.QueryValueEx(key, "Counter")
            winreg.CloseKey(key)

            # Mapear nombres a índices
            items = counter_data.split("\x00")
            idx_map = {}
            for i in range(0, len(items)-1, 2):
                idx_map[items[i+1].lower()] = items[i]

            ni_idx   = idx_map.get("network interface", "510")
            rx_idx   = idx_map.get("bytes received/sec", "264")
            tx_idx   = idx_map.get("bytes sent/sec", "272")
            tot_idx  = idx_map.get("bytes total/sec", "388")
        except Exception:
            ni_idx, rx_idx, tx_idx = "510", "264", "272"

        # Leer acumulados con typeperf (1 muestra instantánea)
        # typeperf devuelve el valor por segundo directamente para estos contadores
        # Preferimos leer bytes totales y hacer la delta nosotros mismos
        # usando GetIfTable2 via ctypes (más rápido aún)
        return self._read_bytes_ctypes()

    def _read_bytes_ctypes(self):
        """
        Usa iphlpapi.GetIfTable2 via ctypes para leer bytes RX/TX de todas
        las interfaces de red — instantáneo, sin subprocesos.
        """
        try:
            import ctypes
            import ctypes.wintypes

            iphlp = ctypes.windll.iphlpapi

            # Puntero a MIB_IF_TABLE2
            class MIB_IF_ROW2(ctypes.Structure):
                _fields_ = [
                    ("InterfaceLuid",        ctypes.c_uint64),
                    ("InterfaceIndex",       ctypes.c_ulong),
                    ("InterfaceGuid",        ctypes.c_byte * 16),
                    ("Alias",                ctypes.c_wchar * 257),
                    ("Description",          ctypes.c_wchar * 257),
                    ("PhysicalAddressLength",ctypes.c_ulong),
                    ("PhysicalAddress",      ctypes.c_byte * 32),
                    ("PermanentPhysicalAddress", ctypes.c_byte * 32),
                    ("Mtu",                  ctypes.c_ulong),
                    ("Type",                 ctypes.c_ulong),
                    ("TunnelType",           ctypes.c_uint),
                    ("MediaType",            ctypes.c_uint),
                    ("PhysicalMediumType",   ctypes.c_uint),
                    ("AccessType",           ctypes.c_uint),
                    ("DirectionType",        ctypes.c_uint),
                    ("InterfaceAndOperStatusFlags", ctypes.c_byte),
                    ("OperStatus",           ctypes.c_uint),
                    ("AdminStatus",          ctypes.c_uint),
                    ("MediaConnectState",    ctypes.c_uint),
                    ("NetworkGuid",          ctypes.c_byte * 16),
                    ("ConnectionType",       ctypes.c_uint),
                    ("TransmitLinkSpeed",    ctypes.c_uint64),
                    ("ReceiveLinkSpeed",     ctypes.c_uint64),
                    ("InOctets",             ctypes.c_uint64),
                    ("InUcastPkts",          ctypes.c_uint64),
                    ("InNUcastPkts",         ctypes.c_uint64),
                    ("InDiscards",           ctypes.c_uint64),
                    ("InErrors",             ctypes.c_uint64),
                    ("InUnknownProtos",      ctypes.c_uint64),
                    ("InUcastOctets",        ctypes.c_uint64),
                    ("InMulticastOctets",    ctypes.c_uint64),
                    ("InBroadcastOctets",    ctypes.c_uint64),
                    ("OutOctets",            ctypes.c_uint64),
                    ("OutUcastPkts",         ctypes.c_uint64),
                    ("OutNUcastPkts",        ctypes.c_uint64),
                    ("OutDiscards",          ctypes.c_uint64),
                    ("OutErrors",            ctypes.c_uint64),
                    ("OutUcastOctets",       ctypes.c_uint64),
                    ("OutMulticastOctets",   ctypes.c_uint64),
                    ("OutBroadcastOctets",   ctypes.c_uint64),
                    ("OutQLen",              ctypes.c_uint64),
                ]

            class MIB_IF_TABLE2(ctypes.Structure):
                _fields_ = [
                    ("NumEntries", ctypes.c_ulong),
                    ("Table",      MIB_IF_ROW2 * 256),
                ]

            p_table = ctypes.POINTER(MIB_IF_TABLE2)()
            ret = iphlp.GetIfTable2(ctypes.byref(p_table))
            if ret != 0:
                return None

            table = p_table.contents
            rx_total, tx_total = 0, 0
            found_wifi = False

            for i in range(table.NumEntries):
                row = table.Table[i]
                desc = row.Description.lower()
                alias = row.Alias.lower()
                # Filtrar loopback y tunelado
                if row.Type in (24, 131):   # loopback, tunnel
                    continue
                # Buscar WiFi
                is_wifi = any(k in desc or k in alias for k in
                              ["wi-fi", "wifi", "wlan", "wireless", "802.11",
                               "ralink", "realtek wireless", "mediatek", "tp-link",
                               "edimax", "alfa", "comfast"])
                if is_wifi:
                    rx_total += row.InOctets
                    tx_total += row.OutOctets
                    found_wifi = True

            if not found_wifi:
                # Fallback: sumar todas las interfaces activas no-loopback
                for i in range(table.NumEntries):
                    row = table.Table[i]
                    if row.Type in (24, 131):
                        continue
                    if row.OperStatus == 1:   # IfOperStatusUp
                        rx_total += row.InOctets
                        tx_total += row.OutOctets

            try:
                iphlp.FreeMibTable(p_table)
            except Exception:
                pass

            return rx_total, tx_total

        except Exception:
            return None

    def _read_bytes_netstat(self):
        """Fallback: netstat -e."""
        out, _ = run_cmd("netstat -e", timeout=3)
        for line in out.splitlines():
            if "bytes" in line.lower():
                nums = re.findall(r"\d{4,}", line)
                if len(nums) >= 2:
                    return int(nums[0]), int(nums[1])
        return None

    def _read_bytes(self):
        """Lee bytes acumulados RX/TX con el mejor método disponible."""
        r = self._read_bytes_ctypes()
        if r is not None:
            return r
        r = self._read_bytes_netstat()
        return r

    # ── Hilo de muestreo ─────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thr = threading.Thread(target=self._loop, daemon=True)
        self._thr.start()

    def stop(self):
        self._running = False

    def _loop(self):
        prev_rx, prev_tx, prev_t = None, None, None

        while self._running:
            t = time.time()
            r = self._read_bytes()

            if r is not None:
                rx, tx = r
                if prev_rx is not None:
                    dt = t - prev_t
                    if dt >= 0.2:
                        d_rx = max(0, rx - prev_rx)
                        d_tx = max(0, tx - prev_tx)
                        down = round((d_rx * 8) / (dt * 1_000_000), 3)
                        up   = round((d_tx * 8) / (dt * 1_000_000), 3)
                        with self._lock:
                            self._last = (down, up)
                            self._history.append((down, up))
                        self._cb(down, up)
                prev_rx, prev_tx, prev_t = rx, tx, t

            # Dormir ajustando por tiempo de ejecución
            elapsed = time.time() - t
            sleep_t = max(0.05, self.INTERVAL - elapsed)
            time.sleep(sleep_t)

    def get_history(self):
        with self._lock:
            return list(self._history)

    def get_last(self):
        with self._lock:
            return self._last



def fmt_speed(mbps):
    """Formatea velocidad de forma legible."""
    if mbps is None:
        return "—"
    if mbps >= 1000:
        return f"{mbps/1000:.1f} Gbps"
    if mbps >= 1:
        return f"{mbps:.1f} Mbps"
    kbps = mbps * 1000
    return f"{kbps:.0f} Kbps"

def analyze_disconnect_reason(prev_state, curr_state, signal, latency):
    """Analiza posibles causas de desconexión."""
    reasons = []
    if not curr_state.get("internet"):
        if signal and signal < 30:
            reasons.append(f"⚠ Señal WiFi muy débil ({signal}%) - Alejar interferencias o acercar al router")
        if signal and signal < 60:
            reasons.append(f"⚠ Señal moderada ({signal}%) - Posible interferencia de canal o distancia")
        if not curr_state.get("adapter_present"):
            reasons.append("🔌 Adaptador USB no detectado - Reconectar el dispositivo USB")
        if curr_state.get("adapter_present") and not curr_state.get("wifi_connected"):
            reasons.append("📡 Adaptador USB presente pero sin conexión WiFi - Verificar red/contraseña")
        if curr_state.get("wifi_connected") and not curr_state.get("internet"):
            reasons.append("🌐 WiFi conectado pero sin internet - Problema en el router o ISP")
        if latency and latency > 500:
            reasons.append(f"⏱ Latencia muy alta ({latency}ms) - Red congestionada")
        if not curr_state.get("dns"):
            reasons.append("🔍 Fallo DNS - Posible problema con servidor DNS o red")
    return reasons


# ─── Motor de Monitorización ──────────────────────────────────────────────────

class MonitorEngine:
    def __init__(self, callback, speed_callback):
        self.callback       = callback        # estado general cada ~3s
        self.speed_callback = speed_callback  # velocidad cada ~1s
        self.running  = False
        self.thread   = None
        self.history  = deque(maxlen=HISTORY_SIZE)
        self.latency_data = deque(maxlen=LATENCY_HISTORY)
        self.prev_state   = {}
        self.uptime_start   = None
        self.downtime_start = None
        self.total_downtime = 0
        self.disconnect_events = []
        self.speed_meter = SpeedMeter(speed_callback)  # hilo propio a 1s

    def start(self):
        self.running = True
        self.speed_meter.start()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.speed_meter.stop()

    def _loop(self):
        first = True
        while self.running:
            state = self._collect()
            self.history.append(state)
            self.latency_data.append(state.get("latency"))

            was_online = self.prev_state.get("internet", None)
            is_online  = state.get("internet", False)

            # Primera iteración: arrancar uptime si ya hay internet
            if first:
                first = False
                if is_online and self.uptime_start is None:
                    self.uptime_start = time.time()

            elif was_online is not None:
                if was_online and not is_online:
                    # Conexión perdida
                    self.downtime_start = time.time()
                    reasons = analyze_disconnect_reason(self.prev_state, state,
                                                        state.get("signal"), state.get("latency"))
                    event = {
                        "time": datetime.datetime.now().strftime("%H:%M:%S"),
                        "type": "DESCONEXIÓN",
                        "reasons": reasons,
                        "signal": state.get("signal"),
                        "adapter": state.get("adapter_present"),
                    }
                    self.disconnect_events.append(event)
                    self._log_event(event)

                elif not was_online and is_online:
                    # Reconexión
                    if self.downtime_start:
                        dt = time.time() - self.downtime_start
                        self.total_downtime += dt
                        event = {
                            "time": datetime.datetime.now().strftime("%H:%M:%S"),
                            "type": "RECONEXIÓN",
                            "downtime": f"{dt:.1f}s",
                        }
                        self.disconnect_events.append(event)
                        self._log_event(event)
                        self.downtime_start = None
                    if self.uptime_start is None:
                        self.uptime_start = time.time()

            self.prev_state = state
            self.callback(state, list(self.latency_data),
                          self.disconnect_events, self.total_downtime)
            time.sleep(CHECK_INTERVAL)

    def _collect(self):
        state = {"timestamp": datetime.datetime.now()}

        # ── USB / WiFi adapter ──
        usb = detect_usb_adapter()
        state["adapter_present"] = len(usb) > 0
        state["usb_adapters"] = usb

        if usb:
            a = usb[0]
            state["adapter_name"] = a.get("Name") or a.get("Description") or "—"
            state["adapter_mac"]  = a.get("MACAddress") or "—"
            state["adapter_desc"] = a.get("Description") or a.get("Name") or "—"
        else:
            state["adapter_name"] = "—"
            state["adapter_mac"]  = "—"
            state["adapter_desc"] = "—"

        # ── WiFi interfaces (netsh) → parser robusto ──
        raw = run_cmd("netsh wlan show interfaces")[0]
        state["raw_wlan"] = raw
        wi = parse_netsh_interfaces(raw)

        state["wifi_connected"] = wi.get("connected", False)
        state["signal"]     = wi.get("signal")
        state["ssid"]       = wi.get("ssid")
        state["bssid"]      = wi.get("bssid")
        state["channel"]    = wi.get("channel")
        state["radio_type"] = wi.get("radio_type")
        state["tx_rate"]    = wi.get("tx_rate")
        state["rx_rate"]    = wi.get("rx_rate")

        # MAC desde netsh si no la tenemos del USB
        if state["adapter_mac"] == "—" and wi.get("mac"):
            state["adapter_mac"] = wi["mac"]

        # ── Internet / Ping ──
        latency = None
        for host in PING_HOSTS:
            lat = ping(host)
            if lat is not None:
                latency = lat
                state["ping_host"] = host
                break
        state["latency"]  = latency
        state["internet"] = latency is not None

        # ── DNS ──
        dns_ok, dns_ms = get_dns_status()
        state["dns"]    = dns_ok
        state["dns_ms"] = dns_ms

        # ── IP / Gateway (PowerShell + fallback ipconfig) ──
        ip, gw = get_ip_info()
        state["local_ip"] = ip or "—"
        state["gateway"]  = gw or "—"

        return state

    def _log_event(self, event):
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n[{event['time']}] {event['type']}\n")
                if "reasons" in event:
                    for r in event["reasons"]:
                        f.write(f"  {r}\n")
                if "downtime" in event:
                    f.write(f"  Tiempo sin conexión: {event['downtime']}\n")
        except Exception:
            pass


# ─── Interfaz Gráfica ─────────────────────────────────────────────────────────

class WiFiMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"WiFi USB Monitor v{VERSION}")
        self.root.geometry("980x780")
        self.root.minsize(820, 600)
        self.root.configure(bg=C["bg"])

        # Icono
        try:
            self.root.iconbitmap(default='')
        except Exception:
            pass

        self.q       = queue.Queue()
        self.speed_q = queue.Queue(maxsize=200)
        self.monitor = MonitorEngine(self._on_data, self._on_speed)
        self.is_monitoring  = False
        self.canvas_data    = []
        self.speed_canvas_data = []

        self._build_ui()
        self._process_queue()

    def _build_ui(self):
        # ── Header ──
        header = tk.Frame(self.root, bg=C["bg"], height=60)
        header.pack(fill="x", padx=0, pady=0)
        header.pack_propagate(False)

        tk.Label(header, text="⬡", font=("Courier New", 28, "bold"),
                 bg=C["bg"], fg=C["blue"]).pack(side="left", padx=(18,6), pady=8)
        tk.Label(header, text="WiFi USB Monitor",
                 font=("Consolas", 20, "bold"), bg=C["bg"], fg=C["text"]).pack(side="left", pady=8)
        tk.Label(header, text=f"v{VERSION}",
                 font=("Consolas", 10), bg=C["bg"], fg=C["text_dim"]).pack(side="left", padx=8, pady=14)

        # Status pill
        self.lbl_status_pill = tk.Label(header, text="  ● DETENIDO  ",
                                         font=("Consolas", 11, "bold"),
                                         bg=C["bg3"], fg=C["text_dim"],
                                         relief="flat", padx=8, pady=4)
        self.lbl_status_pill.pack(side="right", padx=18, pady=12)

        # Botones
        self.btn_toggle = tk.Button(header, text="▶  INICIAR",
                                    font=("Consolas", 11, "bold"),
                                    bg=C["green"], fg=C["bg"],
                                    relief="flat", padx=14, pady=4,
                                    cursor="hand2",
                                    command=self._toggle_monitor)
        self.btn_toggle.pack(side="right", padx=4, pady=12)

        tk.Button(header, text="📋 Ver Log",
                  font=("Consolas", 10),
                  bg=C["bg3"], fg=C["text"],
                  relief="flat", padx=10, pady=4,
                  cursor="hand2",
                  command=self._open_log).pack(side="right", padx=4, pady=12)

        tk.Button(header, text="🔬 Diagnóstico",
                  font=("Consolas", 10),
                  bg=C["bg3"], fg=C["purple"],
                  relief="flat", padx=10, pady=4,
                  cursor="hand2",
                  command=self._open_diagnostic).pack(side="right", padx=4, pady=12)

        # Separador
        tk.Frame(self.root, bg=C["border"], height=1).pack(fill="x")

        # ── Cuerpo principal ──
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # Columna izquierda
        left = tk.Frame(body, bg=C["bg"], width=320)
        left.pack(side="left", fill="y", padx=(0,10))
        left.pack_propagate(False)

        self._build_status_card(left)
        self._build_adapter_card(left)
        self._build_network_card(left)

        # Columna derecha
        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        self._build_graph_card(right)
        self._build_speed_card(right)
        self._build_log_card(right)

    def _card(self, parent, title, icon=""):
        frame = tk.Frame(parent, bg=C["bg2"], bd=0,
                         highlightbackground=C["border"], highlightthickness=1)
        frame.pack(fill="x", pady=(0,10))
        tk.Label(frame, text=f" {icon}  {title}",
                 font=("Consolas", 10, "bold"),
                 bg=C["bg3"], fg=C["blue"], anchor="w",
                 padx=8, pady=5).pack(fill="x")
        content = tk.Frame(frame, bg=C["bg2"], padx=10, pady=8)
        content.pack(fill="both", expand=True)
        return content

    def _stat_row(self, parent, label, var, row):
        tk.Label(parent, text=label, font=("Consolas", 9),
                 bg=C["bg2"], fg=C["text_dim"], anchor="w").grid(
                 row=row, column=0, sticky="w", pady=2)
        lbl = tk.Label(parent, textvariable=var, font=("Consolas", 9, "bold"),
                        bg=C["bg2"], fg=C["text"], anchor="e")
        lbl.grid(row=row, column=1, sticky="e", pady=2, padx=(8,0))
        parent.columnconfigure(1, weight=1)
        return lbl

    def _build_status_card(self, parent):
        c = self._card(parent, "ESTADO DE CONEXIÓN", "🌐")

        # Indicador grande
        ind_frame = tk.Frame(c, bg=C["bg2"])
        ind_frame.pack(fill="x", pady=(4, 10))

        self.ind_canvas = tk.Canvas(ind_frame, width=18, height=18,
                                    bg=C["bg2"], highlightthickness=0)
        self.ind_canvas.pack(side="left", padx=(0,8))
        self.ind_dot = self.ind_canvas.create_oval(2,2,16,16, fill=C["text_dim"], outline="")

        self.lbl_conn = tk.Label(ind_frame, text="Sin monitorizar",
                                  font=("Consolas", 14, "bold"),
                                  bg=C["bg2"], fg=C["text_dim"])
        self.lbl_conn.pack(side="left")

        # Stats
        g = tk.Frame(c, bg=C["bg2"])
        g.pack(fill="x")

        self.v_latency = tk.StringVar(value="—")
        self.v_dns = tk.StringVar(value="—")
        self.v_uptime = tk.StringVar(value="—")
        self.v_downtime_total = tk.StringVar(value="—")

        self._stat_row(g, "Latencia (ping):", self.v_latency, 0)
        self._stat_row(g, "DNS:", self.v_dns, 1)
        self._stat_row(g, "Tiempo online:", self.v_uptime, 2)
        self._stat_row(g, "Total offline:", self.v_downtime_total, 3)

        # Barra de señal
        tk.Label(c, text="Señal WiFi:", font=("Consolas", 9),
                 bg=C["bg2"], fg=C["text_dim"], anchor="w").pack(fill="x", pady=(8,2))
        bar_bg = tk.Frame(c, bg=C["border"], height=16, relief="flat")
        bar_bg.pack(fill="x")
        bar_bg.pack_propagate(False)
        self.signal_bar = tk.Frame(bar_bg, bg=C["text_dim"], height=16)
        self.signal_bar.place(x=0, y=0, relheight=1, relwidth=0)
        self.lbl_signal_pct = tk.Label(bar_bg, text="—", font=("Consolas", 8, "bold"),
                                        bg=C["text_dim"], fg=C["bg"])
        self.lbl_signal_pct.place(relx=0.5, rely=0.5, anchor="center")

    def _build_adapter_card(self, parent):
        c = self._card(parent, "ADAPTADOR USB WiFi", "🔌")
        g = tk.Frame(c, bg=C["bg2"])
        g.pack(fill="x")

        self.v_usb_status = tk.StringVar(value="—")
        self.v_usb_name   = tk.StringVar(value="—")
        self.v_usb_desc   = tk.StringVar(value="—")
        self.v_mac        = tk.StringVar(value="—")
        self.v_ssid       = tk.StringVar(value="—")
        self.v_bssid      = tk.StringVar(value="—")

        l0 = self._stat_row(g, "USB detectado:", self.v_usb_status, 0)
        self.lbl_usb_status = l0
        self._stat_row(g, "Adaptador:", self.v_usb_name, 1)
        self._stat_row(g, "Descripción:", self.v_usb_desc, 2)
        self._stat_row(g, "MAC:", self.v_mac, 3)
        self._stat_row(g, "SSID:", self.v_ssid, 4)
        self._stat_row(g, "BSSID:", self.v_bssid, 5)

    def _build_network_card(self, parent):
        c = self._card(parent, "PARÁMETROS DE RED", "📡")
        g = tk.Frame(c, bg=C["bg2"])
        g.pack(fill="x")

        self.v_radio = tk.StringVar(value="—")
        self.v_channel = tk.StringVar(value="—")
        self.v_tx = tk.StringVar(value="—")
        self.v_rx = tk.StringVar(value="—")
        self.v_ip = tk.StringVar(value="—")
        self.v_gw = tk.StringVar(value="—")

        self._stat_row(g, "Tipo radio:", self.v_radio, 0)
        self._stat_row(g, "Canal:", self.v_channel, 1)
        self._stat_row(g, "Velocidad TX:", self.v_tx, 2)
        self._stat_row(g, "Velocidad RX:", self.v_rx, 3)
        self._stat_row(g, "IP local:", self.v_ip, 4)
        self._stat_row(g, "Puerta enlace:", self.v_gw, 5)

    def _build_graph_card(self, parent):
        c = self._card(parent, "HISTORIAL DE LATENCIA", "📈")
        self.graph_canvas = tk.Canvas(c, height=110, bg=C["bg3"],
                                      highlightthickness=0)
        self.graph_canvas.pack(fill="x", expand=False)
        self.graph_canvas.bind("<Configure>", self._redraw_graph)

        # Leyenda
        leg = tk.Frame(c, bg=C["bg2"])
        leg.pack(fill="x", pady=(4,0))
        for color, label in [(C["green"], "Online"), (C["red"], "Sin internet"), (C["yellow"], "Latencia alta")]:
            f = tk.Frame(leg, bg=C["bg2"])
            f.pack(side="left", padx=8)
            tk.Frame(f, bg=color, width=14, height=8).pack(side="left", padx=(0,4))
            tk.Label(f, text=label, font=("Consolas", 8), bg=C["bg2"], fg=C["text_dim"]).pack(side="left")

    def _build_speed_card(self, parent):
        c = self._card(parent, "VELOCIDAD INSTANTÁNEA", "⚡")

        # Valores numéricos grandes
        nums = tk.Frame(c, bg=C["bg2"])
        nums.pack(fill="x", pady=(0, 8))
        nums.columnconfigure(0, weight=1)
        nums.columnconfigure(1, weight=1)

        # ── Bajada ──
        down_f = tk.Frame(nums, bg=C["bg3"], padx=10, pady=8)
        down_f.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tk.Label(down_f, text="▼ BAJADA", font=("Consolas", 8, "bold"),
                 bg=C["bg3"], fg=C["cyan"]).pack()
        self.v_down = tk.StringVar(value="—")
        self.lbl_down = tk.Label(down_f, textvariable=self.v_down,
                                  font=("Consolas", 18, "bold"),
                                  bg=C["bg3"], fg=C["cyan"])
        self.lbl_down.pack()
        self.v_down_peak = tk.StringVar(value="pico: —")
        tk.Label(down_f, textvariable=self.v_down_peak,
                 font=("Consolas", 8), bg=C["bg3"], fg=C["text_dim"]).pack()

        # ── Subida ──
        up_f = tk.Frame(nums, bg=C["bg3"], padx=10, pady=8)
        up_f.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        tk.Label(up_f, text="▲ SUBIDA", font=("Consolas", 8, "bold"),
                 bg=C["bg3"], fg=C["pink"]).pack()
        self.v_up = tk.StringVar(value="—")
        self.lbl_up = tk.Label(up_f, textvariable=self.v_up,
                                font=("Consolas", 18, "bold"),
                                bg=C["bg3"], fg=C["pink"])
        self.lbl_up.pack()
        self.v_up_peak = tk.StringVar(value="pico: —")
        tk.Label(up_f, textvariable=self.v_up_peak,
                 font=("Consolas", 8), bg=C["bg3"], fg=C["text_dim"]).pack()

        # ── Gráfico de velocidad ──
        self.speed_canvas = tk.Canvas(c, height=110, bg=C["bg3"],
                                       highlightthickness=0)
        self.speed_canvas.pack(fill="x", expand=False)
        self.speed_canvas.bind("<Configure>", self._redraw_speed_graph)

        # Leyenda velocidad
        leg = tk.Frame(c, bg=C["bg2"])
        leg.pack(fill="x", pady=(4, 0))
        for color, label in [(C["cyan"], "Bajada"), (C["pink"], "Subida")]:
            f = tk.Frame(leg, bg=C["bg2"])
            f.pack(side="left", padx=8)
            tk.Frame(f, bg=color, width=14, height=8).pack(side="left", padx=(0,4))
            tk.Label(f, text=label, font=("Consolas", 8),
                     bg=C["bg2"], fg=C["text_dim"]).pack(side="left")

        self._peak_down = 0.0
        self._peak_up   = 0.0

    def _build_log_card(self, parent):
        c = self._card(parent, "REGISTRO DE EVENTOS", "📋")

        self.log_text = scrolledtext.ScrolledText(
            c, height=14, bg=C["bg3"], fg=C["text"],
            font=("Consolas", 9), relief="flat",
            insertbackground=C["text"],
            selectbackground=C["accent"],
            wrap=tk.WORD
        )
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

        # Tags de color
        self.log_text.tag_configure("error", foreground=C["red"])
        self.log_text.tag_configure("ok", foreground=C["green"])
        self.log_text.tag_configure("warn", foreground=C["yellow"])
        self.log_text.tag_configure("info", foreground=C["blue"])
        self.log_text.tag_configure("dim", foreground=C["text_dim"])
        self.log_text.tag_configure("reason", foreground=C["orange"])

    # ── Lógica de monitorización ──────────────────────────────────────────────

    def _toggle_monitor(self):
        if not self.is_monitoring:
            self.monitor.start()
            self.is_monitoring = True
            self.btn_toggle.config(text="⏹  DETENER", bg=C["red"])
            self.lbl_status_pill.config(text="  ● MONITORIZANDO  ", fg=C["green"])
            self._log_append("[SISTEMA] Monitorización iniciada", "info")
        else:
            self.monitor.stop()
            self.is_monitoring = False
            self.btn_toggle.config(text="▶  INICIAR", bg=C["green"])
            self.lbl_status_pill.config(text="  ● DETENIDO  ", fg=C["text_dim"])
            self._log_append("[SISTEMA] Monitorización detenida", "warn")

    def _on_data(self, state, latency_data, events, total_downtime):
        self.q.put((state, latency_data, events, total_downtime))

    def _on_speed(self, down, up):
        """Llamado desde el hilo de velocidad cada ~1s — encola para la UI."""
        try:
            self.speed_q.put_nowait((down, up))
        except queue.Full:
            pass

    def _process_queue(self):
        # ── Datos generales (cada ~3s) ──
        try:
            while True:
                state, latency_data, events, total_downtime = self.q.get_nowait()
                self._update_ui(state, latency_data, events, total_downtime)
        except queue.Empty:
            pass

        # ── Velocidad (cada ~1s, múltiples muestras posibles) ──
        updated_speed = False
        try:
            while True:
                down, up = self.speed_q.get_nowait()
                self.speed_canvas_data.append((down, up))
                if len(self.speed_canvas_data) > SPEED_HISTORY:
                    self.speed_canvas_data = self.speed_canvas_data[-SPEED_HISTORY:]
                self._update_speed_display(down, up)
                updated_speed = True
        except queue.Empty:
            pass
        if updated_speed:
            self._redraw_speed_graph()

        self.root.after(250, self._process_queue)  # refrescar cada 250ms

    def _update_ui(self, state, latency_data, events, total_downtime):
        # ── Estado conexión ──
        online = state.get("internet", False)
        if online:
            self.lbl_conn.config(text="CONECTADO", fg=C["green"])
            self.ind_canvas.itemconfig(self.ind_dot, fill=C["green"])
        else:
            self.lbl_conn.config(text="SIN INTERNET", fg=C["red"])
            self.ind_canvas.itemconfig(self.ind_dot, fill=C["red"])

        lat = state.get("latency")
        self.v_latency.set(f"{lat}ms" if lat else "—")
        self.v_dns.set("✓ OK" if state.get("dns") else "✗ Fallo")

        # Uptime
        if self.monitor.uptime_start:
            up = int(time.time() - self.monitor.uptime_start)
            h, m, s = up // 3600, (up % 3600) // 60, up % 60
            self.v_uptime.set(f"{h:02d}:{m:02d}:{s:02d}")
        td = int(total_downtime)
        self.v_downtime_total.set(f"{td}s" if td > 0 else "0s")

        # ── Señal ──
        sig = state.get("signal")
        if sig is not None:
            pct = sig / 100.0
            color = C["green"] if sig >= 60 else (C["yellow"] if sig >= 30 else C["red"])
            self.signal_bar.place(relwidth=pct)
            self.signal_bar.config(bg=color)
            self.lbl_signal_pct.config(text=f"{sig}%", bg=color)
        else:
            self.signal_bar.place(relwidth=0)
            self.lbl_signal_pct.config(text="—", bg=C["text_dim"])

        # ── Adaptador USB ──
        usb_present = state.get("adapter_present", False)
        if usb_present:
            self.v_usb_status.set("✓ Detectado")
            self.lbl_usb_status.config(fg=C["green"])
            name = state.get("adapter_name", "—")
            desc = state.get("adapter_desc", "—")
            mac  = state.get("adapter_mac",  "—")
            self.v_usb_name.set(name[:30] if name else "—")
            self.v_usb_desc.set(desc[:30] if desc and desc != name else "—")
            self.v_mac.set(mac if mac else "—")
        else:
            self.v_usb_status.set("✗ No detectado")
            self.lbl_usb_status.config(fg=C["red"])
            self.v_usb_name.set("—")
            self.v_usb_desc.set("—")
            self.v_mac.set("—")

        self.v_ssid.set(state.get("ssid") or "—")
        self.v_bssid.set((state.get("bssid") or "—")[:20])

        # ── Red ──
        self.v_radio.set(state.get("radio_type") or "—")
        self.v_channel.set(str(state.get("channel")) if state.get("channel") else "—")
        self.v_tx.set(state.get("tx_rate") or "—")
        self.v_rx.set(state.get("rx_rate") or "—")
        self.v_ip.set(state.get("local_ip") or "—")
        self.v_gw.set(state.get("gateway") or "—")

        # ── Gráfico latencia ──
        self.canvas_data = latency_data
        self._redraw_graph()

        # ── Log de eventos ──
        # Buscar nuevos eventos
        if events:
            last = events[-1]
            ts = last.get("time", "")
            etype = last.get("type", "")
            if etype == "DESCONEXIÓN":
                self._log_append(f"\n[{ts}] ⚡ DESCONEXIÓN DETECTADA", "error")
                for r in last.get("reasons", []):
                    self._log_append(f"  {r}", "reason")
            elif etype == "RECONEXIÓN":
                dt = last.get("downtime", "?")
                self._log_append(f"[{ts}] ✓ RECONEXIÓN — estuvo offline {dt}", "ok")

    def _update_speed_display(self, down, up):
        """Actualiza los valores numéricos de velocidad (llamado cada ~1s)."""
        self.v_down.set(fmt_speed(down))
        self.v_up.set(fmt_speed(up))
        if down is not None and down > self._peak_down:
            self._peak_down = down
            self.v_down_peak.set(f"pico: {fmt_speed(down)}")
        if up is not None and up > self._peak_up:
            self._peak_up = up
            self.v_up_peak.set(f"pico: {fmt_speed(up)}")

    def _redraw_graph(self, event=None):
        c = self.graph_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return

        data = self.canvas_data
        pad = 8
        inner_w = w - pad * 2
        inner_h = h - pad * 2

        # Grilla
        for i in range(5):
            y = pad + i * inner_h // 4
            c.create_line(pad, y, w - pad, y, fill=C["border"], dash=(3, 4))

        if not data:
            c.create_text(w // 2, h // 2, text="Sin datos aún...",
                          fill=C["text_dim"], font=("Consolas", 10))
            return

        # Filtrar Nones y calcular rango
        valid = [v for v in data if v is not None]
        if not valid:
            return

        max_val = max(max(valid), 200)
        n = len(data)
        step = inner_w / max(n - 1, 1)

        # Dibujar puntos y líneas
        points = []
        for i, val in enumerate(data):
            x = pad + i * step
            if val is None:
                y = h - pad
                color = C["red"]
            else:
                y = pad + inner_h - (val / max_val) * inner_h
                color = C["yellow"] if val > 200 else C["green"]
            points.append((x, y, val is None, color))

        # Área rellena
        fill_pts = [pad, h - pad]
        for x, y, is_none, _ in points:
            fill_pts.extend([x, y])
        fill_pts.extend([w - pad, h - pad])
        c.create_polygon(fill_pts, fill=C["green_dim"], outline="", stipple="")

        # Línea
        for i in range(1, len(points)):
            x0, y0, n0, c0 = points[i - 1]
            x1, y1, n1, c1 = points[i]
            col = C["red"] if n0 or n1 else (C["yellow"] if (y0 < h * 0.4 or y1 < h * 0.4) else C["green"])
            c.create_line(x0, y0, x1, y1, fill=col, width=2, smooth=True)

        # Último valor
        if valid:
            last_val = valid[-1]
            c.create_text(w - pad - 2, pad + 2,
                          text=f"{last_val}ms",
                          fill=C["text"], font=("Consolas", 8, "bold"),
                          anchor="ne")

        # Etiquetas eje Y
        for label, frac in [("0ms", 1.0), (f"{max_val//2}ms", 0.5), (f"{max_val}ms", 0.0)]:
            y = pad + inner_h * frac
            c.create_text(pad + 2, y, text=label,
                          fill=C["text_dim"], font=("Consolas", 7), anchor="w")

    def _redraw_speed_graph(self, event=None):
        c = self.speed_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return

        data = self.speed_canvas_data  # lista de (down, up) o None
        pad = 8
        inner_w = w - pad * 2
        inner_h = h - pad * 2

        # Grilla
        for i in range(5):
            y = pad + i * inner_h // 4
            c.create_line(pad, y, w - pad, y, fill=C["border"], dash=(3, 4))

        if not data:
            c.create_text(w // 2, h // 2, text="Sin datos aún...",
                          fill=C["text_dim"], font=("Consolas", 10))
            return

        downs = [d[0] for d in data if d and d[0] is not None]
        ups   = [d[1] for d in data if d and d[1] is not None]
        if not downs and not ups:
            c.create_text(w // 2, h // 2, text="Midiendo velocidad...",
                          fill=C["text_dim"], font=("Consolas", 10))
            return

        all_vals = downs + ups
        max_val = max(max(all_vals), 0.1)
        n = len(data)
        step = inner_w / max(n - 1, 1)

        def draw_series(series_idx, color, fill_color):
            pts_line = []
            fill_pts  = [pad, h - pad]
            for i, pair in enumerate(data):
                x = pad + i * step
                val = pair[series_idx] if (pair and pair[series_idx] is not None) else None
                if val is not None:
                    y = pad + inner_h - (val / max_val) * inner_h
                else:
                    y = h - pad
                pts_line.append((x, y, val is None))
                fill_pts.extend([x, y])
            fill_pts.extend([w - pad, h - pad])
            c.create_polygon(fill_pts, fill=fill_color, outline="")
            for i in range(1, len(pts_line)):
                x0, y0, n0 = pts_line[i-1]
                x1, y1, n1 = pts_line[i]
                if not n0 and not n1:
                    c.create_line(x0, y0, x1, y1, fill=color, width=2, smooth=True)

        draw_series(0, C["cyan"], C["cyan_dim"])   # bajada
        draw_series(1, C["pink"], C["pink_dim"])   # subida

        # Último valor en esquinas
        if downs:
            c.create_text(w - pad - 2, pad + 2,
                          text=f"▼{fmt_speed(downs[-1])}",
                          fill=C["cyan"], font=("Consolas", 8, "bold"), anchor="ne")
        if ups:
            c.create_text(w - pad - 2, pad + 14,
                          text=f"▲{fmt_speed(ups[-1])}",
                          fill=C["pink"], font=("Consolas", 8, "bold"), anchor="ne")

        # Eje Y
        for label, frac in [("0", 1.0), (fmt_speed(max_val/2), 0.5), (fmt_speed(max_val), 0.0)]:
            y = pad + inner_h * frac
            c.create_text(pad + 2, y, text=label,
                          fill=C["text_dim"], font=("Consolas", 7), anchor="w")

    def _log_append(self, text, tag="dim"):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _open_log(self):
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)
        else:
            messagebox.showinfo("Log", f"Aún no hay eventos registrados.\nSe guardará en:\n{LOG_FILE}")

    def _open_diagnostic(self):
        """Ventana de diagnóstico con output raw de comandos."""
        win = tk.Toplevel(self.root)
        win.title("Diagnóstico del sistema")
        win.geometry("720x520")
        win.configure(bg=C["bg"])

        tk.Label(win, text="🔬  Diagnóstico del Adaptador WiFi USB",
                 font=("Consolas", 12, "bold"), bg=C["bg"], fg=C["blue"]).pack(pady=10)

        txt = scrolledtext.ScrolledText(win, bg=C["bg3"], fg=C["text"],
                                         font=("Consolas", 9), relief="flat")
        txt.pack(fill="both", expand=True, padx=10, pady=(0,10))

        def run_diag():
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", "Ejecutando diagnóstico, espera...\n\n")
            txt.configure(state="disabled")
            win.update()

            lines = []

            lines.append("=" * 60)
            lines.append("  netsh wlan show interfaces  (raw)")
            lines.append("=" * 60)
            out, _ = run_cmd("netsh wlan show interfaces")
            lines.append(out if out else "(sin salida)")

            lines.append("\n" + "=" * 60)
            lines.append("  Claves detectadas por el parser interno")
            lines.append("=" * 60)
            parsed = parse_netsh_interfaces(out)
            for k, v in parsed.items():
                lines.append(f"  {k:15s} = {v}")

            lines.append("\n" + "=" * 60)
            lines.append("  Get-NetIPConfiguration (IP / Gateway)")
            lines.append("=" * 60)
            ip, gw = get_ip_info()
            lines.append(f"  IP local  : {ip or '(no encontrada)'}")
            lines.append(f"  Gateway   : {gw or '(no encontrado)'}")

            lines.append("\n" + "=" * 60)
            lines.append("  Get-NetAdapter (PowerShell)")
            lines.append("=" * 60)
            out2, _ = run_ps(
                "Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,MacAddress,LinkSpeed | Format-List"
            )
            lines.append(out2 if out2 else "(sin salida)")

            lines.append("\n" + "=" * 60)
            lines.append("  Adaptadores con 'wireless/usb/wifi/wlan/802' en descripción")
            lines.append("=" * 60)
            out3, _ = run_ps(
                "Get-NetAdapter | Where-Object { $_.InterfaceDescription -match 'wireless|wifi|usb|wlan|802' } | Format-List"
            )
            lines.append(out3 if out3 else "(Ninguno — tu adaptador puede tener otro nombre)")

            lines.append("\n" + "=" * 60)
            lines.append("  TODOS los adaptadores (para identificar el tuyo)")
            lines.append("=" * 60)
            out4, _ = run_ps(
                "Get-NetAdapter | Format-Table Name,InterfaceDescription,Status -AutoSize | Out-String -Width 200"
            )
            lines.append(out4 if out4 else "(sin salida)")

            lines.append("\n" + "=" * 60)
            lines.append("  ipconfig /all (sección WiFi/WLAN)")
            lines.append("=" * 60)
            out5, _ = run_cmd("ipconfig /all")
            in_wifi = False
            blank_count = 0
            for l in out5.splitlines():
                if any(k in l.lower() for k in ["wi-fi", "wifi", "wlan", "wireless", "inalámbr", "inal"]):
                    in_wifi = True
                    blank_count = 0
                if in_wifi:
                    lines.append(l)
                    if l.strip() == "":
                        blank_count += 1
                    if blank_count >= 2:
                        in_wifi = False

            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", "\n".join(lines))
            txt.configure(state="disabled")

        tk.Button(win, text="▶ Ejecutar diagnóstico",
                  font=("Consolas", 10, "bold"),
                  bg=C["blue"], fg=C["bg"],
                  relief="flat", padx=12, pady=4,
                  cursor="hand2",
                  command=lambda: threading.Thread(target=run_diag, daemon=True).start()
                  ).pack(pady=(0,10))

        threading.Thread(target=run_diag, daemon=True).start()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    root.title(f"WiFi USB Monitor v{VERSION}")

    # DPI awareness en Windows
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = WiFiMonitorApp(root)

    # Centrar ventana
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    ww, wh = 980, 780
    root.geometry(f"{ww}x{wh}+{(sw-ww)//2}+{(sh-wh)//2}")

    root.mainloop()


if __name__ == "__main__":
    main()
