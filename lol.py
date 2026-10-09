# -*- coding: utf-8 -*-
# ==================== STANDARD IMPORTS ====================
import sys
import asyncio
import httpx
import random
import json
import socket
import struct
import time
import os
import uuid
import itertools
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ==================== ORIGINAL IMPORTS ====================
from google_play_scraper import app as play_scraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from protobuf_decoder.protobuf_decoder import Parser
from message_ids import MESSAGE_ID_TO_NAME
import thunderFF_pb2
import StartMatch_pb2
import ZyxTeam_pb2           # WAJIB: Lone Wolf proto

# ==================== WEB DASHBOARD ====================
from dashboard_server import bot_state, start_web_dashboard

# ==================== CONFIGURATION ====================
WEB_HOST = "0.0.0.0"
WEB_PORT = int(os.environ.get("SERVER_PORT") or os.environ.get("PORT") or 2000)
ACCOUNTS_FILE = "accounts.json"
TOKEN_CACHE_FILE = "token_cache.json"
DEVICES_FILE = "devices.json"
TOKEN_CACHE_TTL = 1200

START_MATCH_INTERVAL = 4.0
NEW_MATCH_DELAY = 5.0
MAX_MATCH_DURATION = 800
MATCH_IDLE_TIMEOUT = 15.0
MAX_CONCURRENT_MATCHES = 1
PRIORITY_REGIONS = ["BD", "IND", "SG", "TH", "PH", "VN", "MY", "ID", "HK", "TW", "BR", "EU", "RU", "TR", "ME", "NA", "SAC", "US", "SSA"]

# Mode switching
LONE_WOLF_UNLOCK_LEVEL = 3
LONE_WOLF_MODE_ID      = 43
LONE_WOLF_MAP_ID       = 11
BR_MODE_ID             = 1
BR_MAP_ID              = 1

# Squad Auto-Fill — HANYA untuk BR (level < 3)
SQUAD_MODE_ENABLED = True
SQUAD_MAX_MEMBERS = 4
SQUAD_FILL_DELAY = 1.5
SQUAD_ACCEPT_DELAY = 1.0
SQUAD_INVITE_RETRY = 3

SQUAD_AUTO_CREATE = True
SQUAD_AUTO_INVITE = True
SQUAD_AUTO_ACCEPT = True

# EXP refresh + reconnect (disamakan dengan main.py)
EXP_REFRESH_INTERVAL = 25.0
EXP_REFRESH_AFTER_MATCH_DELAY = 9.0
MAX_CONSECUTIVE_PARSE_FAILURES = 5
NON_MATCH_RECONNECT_DELAY = 1.0

# Anti-AFK
ANTI_AFK_FIRE_CHANCE = 0.45
ANTI_AFK_MIN_INTERVAL = 1.8
ANTI_AFK_MAX_INTERVAL = 3.5

FALLBACK_UID = ""
FALLBACK_PASSWORD = ""


# ==================== PERSISTENT DEVICE RANDOMIZER ====================
def _generate_new_device() -> dict:
    device_list = [
        ("Samsung", "SM-G998B", "Adreno (TM) 660", "Android OS 12 / API-31"),
        ("Xiaomi", "2201122G", "Adreno (TM) 730", "Android OS 13 / API-33"),
        ("Realme", "RMX3700", "Mali-G710", "Android OS 14 / API-34"),
        ("OnePlus", "CPH2451", "Adreno (TM) 740", "Android OS 13 / API-33"),
        ("OPPO", "CPH2611", "Adreno (TM) 720", "Android OS 14 / API-34"),
        ("Vivo", "V2203", "Mali-G710", "Android OS 12 / API-31"),
        ("Poco", "M2102J20SG", "Adreno (TM) 660", "Android OS 13 / API-33"),
    ]
    brand, model, gpu, os_ver = random.choice(device_list)
    return {
        "unique_device_id": f"Google|{str(uuid.uuid4())}",
        "brand": brand,
        "model": model,
        "gpu_renderer": gpu,
        "system_software": os_ver,
        "screen_width": random.choice([1080, 1440, 720, 1280]),
        "screen_height": random.choice([2400, 3200, 1600, 2400]),
        "screen_dpi": str(random.randint(300, 420)),
        "memory": random.randint(2800, 6500),
        "processor_details": f"ARM64 FP ASIMD AES VMH | {random.randint(2200, 3200)} | {random.randint(6, 12)}",
        "client_ip": f"{random.randint(103, 223)}.{random.randint(10, 250)}.{random.randint(10, 250)}.{random.randint(10, 250)}"
    }


def sync_devices_with_accounts() -> dict:
    accounts = load_accounts()
    devices = {}
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, "r", encoding="utf-8") as f:
                devices = json.load(f)
                if not isinstance(devices, dict):
                    devices = {}
        except Exception:
            devices = {}

    cached_data = _load_token_cache()
    synced_devices = {}

    for acc in accounts:
        acc_key = None
        aliases = []
        if "uid" in acc and acc["uid"]:
            acc_key = str(acc["uid"]).strip()
            aliases.append(acc_key)
        elif "token" in acc and acc["token"]:
            tok = str(acc["token"]).strip()
            tok_pfx = tok[:16]
            aliases.append(tok_pfx)
            aliases.append(tok)
            cached_entry = cached_data.get(f"tok_{tok[:20]}") or cached_data.get(tok)
            if cached_entry:
                if cached_entry.get("open_id"):
                    aliases.insert(0, str(cached_entry["open_id"]))
                if cached_entry.get("account_id"):
                    aliases.append(str(cached_entry["account_id"]))
            acc_key = aliases[0] if aliases else tok_pfx

        if not acc_key:
            continue

        dev_profile = None
        for a in aliases:
            if a in devices:
                dev_profile = devices[a]
                break
        if not dev_profile and acc_key in devices:
            dev_profile = devices[acc_key]
        if not dev_profile:
            dev_profile = _generate_new_device()
        synced_devices[acc_key] = dev_profile

    try:
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(synced_devices, f, indent=4)
    except Exception as e:
        print_error(f"Failed to save synced devices: {e}")
    return synced_devices


def get_device_for_account(account_identifier: str) -> dict:
    devices = {}
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, "r", encoding="utf-8") as f:
                devices = json.load(f)
                if not isinstance(devices, dict):
                    devices = {}
        except Exception:
            devices = {}

    acc_key = str(account_identifier).strip()
    if acc_key in devices:
        return devices[acc_key]

    new_device = _generate_new_device()
    devices[acc_key] = new_device
    try:
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, indent=4)
    except Exception as e:
        print_error(f"Failed to save device mapping: {e}")
    return new_device


# ==================== CLOUDFLARE DNS ====================
CLOUDFLARE_PRIMARY_DNS = "1.1.1.1"
CLOUDFLARE_SECONDARY_DNS = "1.0.0.1"
_DNS_CACHE: Dict[str, Tuple[str, float]] = {}
_DNS_CACHE_TTL = 300.0

async def resolve_host_cloudflare(hostname: str) -> str:
    if not hostname:
        return hostname
    parts = hostname.split('.')
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return hostname
    now = time.time()
    if hostname in _DNS_CACHE:
        ip, exp = _DNS_CACHE[hostname]
        if now < exp:
            return ip

    def _query_cloudflare(server_ip: str) -> Optional[str]:
        s = None
        try:
            tx_id = random.randint(1000, 65535)
            header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
            qname = b"".join(bytes([len(part)]) + part.encode('ascii') for part in hostname.split('.')) + b"\x00"
            query_pkt = header + qname + struct.pack(">HH", 1, 1)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.2)
            s.sendto(query_pkt, (server_ip, 53))
            resp, _ = s.recvfrom(1024)
            if len(resp) >= 12:
                ancount = struct.unpack(">H", resp[6:8])[0]
                if ancount > 0:
                    offset = 12 + len(qname) + 4
                    for _ in range(ancount):
                        if offset >= len(resp):
                            break
                        if (resp[offset] & 0xC0) == 0xC0:
                            offset += 2
                        else:
                            while offset < len(resp) and resp[offset] != 0:
                                offset += 1 + resp[offset]
                            offset += 1
                        if offset + 10 > len(resp):
                            break
                        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", resp[offset:offset+10])
                        offset += 10
                        if rtype == 1 and rdlen == 4 and offset + 4 <= len(resp):
                            return socket.inet_ntoa(resp[offset:offset+4])
                        offset += rdlen
        except Exception:
            pass
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        return None

    loop = asyncio.get_running_loop()
    ip = await loop.run_in_executor(None, _query_cloudflare, CLOUDFLARE_PRIMARY_DNS)
    if not ip:
        ip = await loop.run_in_executor(None, _query_cloudflare, CLOUDFLARE_SECONDARY_DNS)
    if not ip:
        try:
            ip_info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
            if ip_info:
                ip = ip_info[0][4][0]
        except Exception:
            ip = hostname
    if ip:
        _DNS_CACHE[hostname] = (ip, now + _DNS_CACHE_TTL)
    return ip or hostname


def optimize_tcp_socket(sock: socket.socket):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "SIO_KEEPALIVE_VALS") and os.name == 'nt':
            try:
                sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 2000))
            except Exception:
                pass
        elif hasattr(socket, "TCP_KEEPIDLE"):
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
            except Exception:
                pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
    except Exception:
        pass


async def safe_close_writer(writer):
    if not writer:
        return
    try:
        if not writer.is_closing():
            writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=1.5)
    except Exception:
        pass


def optimize_udp_socket(sock: socket.socket):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
        if hasattr(socket, 'SIO_UDP_CONNRESET') and os.name == 'nt':
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception:
                pass
    except Exception:
        pass


# ==================== NETWORK & CRYPTO ====================
client = httpx.AsyncClient(
    verify=False,
    timeout=15.0,
    limits=httpx.Limits(max_connections=300, max_keepalive_connections=150)
)
_LOGIN_SEMAPHORE = asyncio.Semaphore(4)

headers = {
    'User-Agent': 'UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)',
    'Connection': 'Keep-Alive',
    'Accept-Encoding': 'gzip',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Expect': '100-continue',
    'X-Unity-Version': '2018.4.12f1',
    'X-GA-SV': '1789535859',
    'X-GA': 'v1 1',
    'ReleaseVersion': 'OB55'
}

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'

CRC7_TABLE = bytes([
    0, 9, 18, 27, 36, 45, 54, 63, 72, 65, 90, 83, 108, 101, 126, 119,
    25, 16, 11, 2, 61, 52, 47, 38, 81, 88, 67, 74, 117, 124, 103, 110,
    50, 59, 32, 41, 22, 31, 4, 13, 122, 115, 104, 97, 94, 87, 76, 69,
    43, 34, 57, 48, 15, 6, 29, 20, 99, 106, 113, 120, 71, 78, 85, 92,
    100, 109, 118, 127, 64, 73, 82, 91, 44, 37, 62, 55, 8, 1, 26, 19,
    125, 116, 111, 102, 89, 80, 75, 66, 53, 60, 39, 46, 17, 24, 3, 10,
    86, 95, 68, 77, 114, 123, 96, 105, 30, 23, 12, 5, 58, 51, 40, 33,
    79, 70, 93, 84, 107, 98, 121, 112, 7, 14, 21, 28, 35, 42, 49, 56,
    65, 72, 83, 90, 101, 108, 119, 126, 9, 0, 27, 18, 45, 36, 63, 54,
    88, 81, 74, 67, 124, 117, 110, 103, 16, 25, 2, 11, 52, 61, 38, 47,
    115, 122, 97, 104, 87, 94, 69, 76, 59, 50, 41, 32, 31, 22, 13, 4,
    106, 99, 120, 113, 78, 71, 92, 85, 34, 43, 48, 57, 6, 15, 20, 29,
    37, 44, 55, 62, 1, 8, 19, 26, 109, 100, 127, 118, 73, 64, 91, 82,
    60, 53, 46, 39, 24, 17, 10, 3, 116, 125, 102, 111, 80, 89, 66, 75,
    23, 30, 5, 12, 51, 58, 33, 40, 95, 86, 77, 68, 123, 114, 105, 96,
    14, 7, 28, 21, 42, 35, 56, 49, 70, 79, 84, 93, 98, 107, 112, 121,
])

_DELTA = 0x9E3779B9
_ROUNDS = 16
_FIELD_SIZES = {0: 1, 1: 2, 2: 2, 3: 1, 4: 2}
_FIELD_NAMES = {0: "sendOption", 1: "cmd", 2: "orderId", 3: "flags", 4: "length"}

class Colors:
    HEADER = '\033[95m'
    GREEN = '\033[92m'
    FAIL = '\033[91m'
    WARNING = '\033[93m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    WHITE = '\033[97m'
    ENDC = '\033[0m'

def print_colored(text, color=Colors.WHITE):
    try:
        print(f"{color}{text}{Colors.ENDC}")
    except Exception:
        try:
            print(f"{color}{text.encode('ascii', errors='replace').decode('ascii')}{Colors.ENDC}")
        except Exception:
            pass

def print_success(text):
    print_colored(f"[+] {text}", Colors.GREEN)
    try:
        bot_state.log(text, "success")
    except Exception:
        pass

def print_error(text):
    print_colored(f"[-] {text}", Colors.FAIL)
    try:
        bot_state.log(text, "error")
    except Exception:
        pass

def print_warning(text):
    print_colored(f"[!] {text}", Colors.WARNING)
    try:
        bot_state.log(text, "warning")
    except Exception:
        pass

def print_info(text):
    print_colored(f"[i] {text}", Colors.CYAN)
    try:
        bot_state.log(text, "info")
    except Exception:
        pass

def get_proto_field(d, key, default=None):
    if not d or not isinstance(d, dict):
        return default
    if key in d:
        val = d[key].get('data')
        return val if val is not None else default
    if str(key) in d:
        val = d[str(key)].get('data')
        return val if val is not None else default
    return default


# ==================== PER-ACCOUNT MATCH COUNTER ====================
_match_counters: Dict[str, int] = {}
_match_counter_lock = asyncio.Lock()

async def _inc_match(uid: str) -> int:
    async with _match_counter_lock:
        _match_counters[uid] = _match_counters.get(uid, 0) + 1
        return _match_counters[uid]

async def _dec_match(uid: str) -> int:
    async with _match_counter_lock:
        if uid in _match_counters and _match_counters[uid] > 0:
            _match_counters[uid] -= 1
        return _match_counters.get(uid, 0)

async def _get_match_count(uid: str) -> int:
    async with _match_counter_lock:
        return _match_counters.get(uid, 0)


# ==================== TOKEN CACHE ====================
_token_cache_memo: Dict[str, Any] = {}
_token_cache_memo_time: float = 0.0
_TOKEN_CACHE_MEMO_TTL = 5.0

def _json_serializer(obj):
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_hex__": bytes(obj).hex()}
    raise TypeError(f"Type {type(obj)} not serializable")

def _json_deserializer(obj):
    if isinstance(obj, dict):
        if "__bytes_hex__" in obj and len(obj) == 1:
            try:
                return bytes.fromhex(obj["__bytes_hex__"])
            except Exception:
                return b""
        return {k: _json_deserializer(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_deserializer(x) for x in obj]
    return obj

def _load_token_cache() -> Dict[str, Any]:
    global _token_cache_memo, _token_cache_memo_time
    now = time.time()
    if _token_cache_memo and (now - _token_cache_memo_time) < _TOKEN_CACHE_MEMO_TTL:
        return _token_cache_memo
    if not os.path.exists(TOKEN_CACHE_FILE):
        return {}
    try:
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {}
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("Cache root must be dict")
        parsed = _json_deserializer(data)
        _token_cache_memo = parsed
        _token_cache_memo_time = now
        return parsed
    except Exception as e:
        print_error(f"Token cache corrupt -> deleting: {e}")
        try:
            os.remove(TOKEN_CACHE_FILE)
        except Exception:
            pass
        return {}

def _save_token_cache(cache: Dict[str, Any]):
    global _token_cache_memo, _token_cache_memo_time
    try:
        tmp_file = TOKEN_CACHE_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, default=_json_serializer)
        os.replace(tmp_file, TOKEN_CACHE_FILE)
        _token_cache_memo = cache
        _token_cache_memo_time = time.time()
    except Exception as e:
        print_error(f"Token cache save error: {e}")

def cache_get(uid: str) -> Optional[Dict]:
    cache = _load_token_cache()
    entry = cache.get(str(uid))
    if not entry:
        return None
    if time.time() - entry.get("cached_at", 0) > TOKEN_CACHE_TTL:
        print_info(f"[CACHE] UID {uid} expired. Re-login needed.")
        cache_invalidate(uid)
        return None
    if str(entry.get("account_id", "")).isdigit():
        entry["account_id"] = int(entry["account_id"])
    if not isinstance(entry.get("login_payload_data"), (bytes, bytearray)):
        print_warning(f"[CACHE] UID {uid} missing payload -> invalidating")
        cache_invalidate(uid)
        return None
    return entry

def cache_set(uid: str, account_data: Dict):
    cache = _load_token_cache()
    entry = dict(account_data)
    entry["cached_at"] = time.time()
    cache[str(uid)] = entry
    _save_token_cache(cache)
    print_success(f"[CACHE] Saved credentials for UID {uid}")

def cache_invalidate(uid: str):
    cache = _load_token_cache()
    if str(uid) in cache:
        del cache[str(uid)]
        _save_token_cache(cache)
        print_warning(f"[CACHE] Invalidated: {uid}")

def _update_cache_profile(account_data: Dict, level: int, exp: int):
    try:
        auth_uid = account_data.get('auth_uid')
        auth_token = account_data.get('auth_token')
        keys = []
        if auth_uid:
            keys.append(str(auth_uid))
        if auth_token:
            keys.append(f"tok_{auth_token[:20]}")
        if not keys:
            return
        cache = _load_token_cache()
        changed = False
        for k in keys:
            if k in cache and isinstance(cache[k], dict):
                cache[k]['level'] = level
                cache[k]['exp'] = exp
                changed = True
        if changed:
            _save_token_cache(cache)
    except Exception as e:
        print_warning(f"[EXP-REFRESH] cache update failed: {e}")


# ==================== ENCRYPTION & PROTOBUF ====================
async def aes_encrypt(payload, key, iv):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad(payload, AES.block_size))

async def get_playstore_version():
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: play_scraper('com.dts.freefireth', lang='hi', country='id')
        )
        return result.get("version")
    except Exception:
        return "1.132.8"

async def version_config():
    app_version = await get_playstore_version()
    api_url = (
        "https://version.ggwhitehawk.com/live/ver.php"
        f"?version={app_version}"
        "&lang=hi&device=android&channel=android"
        "&appstore=googleplay&region=ID"
        "&whitelist_version=1.3.0&whitelist_sp_version=1.0.0"
    )
    try:
        response = await client.get(api_url)
        response.raise_for_status()
        data = response.json()
        server_url = data.get("server_url")
        remote_version = data.get("remote_version")
        latest_release_version = data.get("latest_release_version")
        if not server_url or not remote_version or not latest_release_version:
            return None
        return latest_release_version, remote_version, server_url
    except Exception as e:
        print_error(f"[VERCONFIG] Error fetching version config: {e}")
        return None

async def get_access_token(uid, password):
    url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    hdrs = {
        "Host": "100067.connect.garena.com",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; SM-G998B Build/SP1A.210812.016)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "close"
    }
    data = {
        "uid": uid,
        "password": password,
        "response_type": "token",
        "client_type": "2",
        "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        "client_id": "100067"
    }
    for attempt in range(5):
        try:
            response = await client.post(url, headers=hdrs, data=data)
            if response.status_code == 200:
                response_data = response.json()
                open_id = response_data.get("open_id")
                access_token = response_data.get("access_token")
                platform = response_data.get("platform", 4)
                if open_id and access_token:
                    return open_id, access_token, platform
            if response.status_code == 429:
                await asyncio.sleep(1)
                continue
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None

async def parse_results(parsed_results):
    result_dict = {}
    for result in parsed_results:
        field_data = {"wire_type": result.wire_type}
        if result.wire_type == "varint":
            field_data["data"] = result.data
        elif result.wire_type == "string":
            field_data["data"] = result.data
        elif result.wire_type == "bytes":
            field_data["data"] = result.data
        elif result.wire_type == "length_delimited":
            if hasattr(result.data, "results"):
                field_data["data"] = await parse_results(result.data.results)
            elif isinstance(result.data, list):
                field_data["data"] = await parse_results(result.data)
            else:
                field_data["data"] = str(result.data)
        result_dict[str(result.field)] = field_data
    return result_dict

async def decode_protobuf(data):
    parsed_results = Parser().parse(data)
    parsed_results_dict = await parse_results(parsed_results)
    return json.dumps(parsed_results_dict)


# ==================== MANUAL PROTOBUF ====================
def encode_varint(n):
    if n < 0:
        return b''
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)


def build_proto(fields):
    parts = []
    for k, v in fields.items():
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    nested = build_proto(item)
                    parts.append(encode_varint((k << 3) | 2) + encode_varint(len(nested)) + nested)
                elif isinstance(item, int):
                    parts.append(encode_varint((k << 3) | 0) + encode_varint(item))
                elif isinstance(item, (str, bytes)):
                    ev = item.encode() if isinstance(item, str) else item
                    parts.append(encode_varint((k << 3) | 2) + encode_varint(len(ev)) + ev)
        elif isinstance(v, dict):
            nested = build_proto(v)
            parts.append(encode_varint((k << 3) | 2) + encode_varint(len(nested)) + nested)
        elif isinstance(v, int):
            parts.append(encode_varint((k << 3) | 0) + encode_varint(v))
        elif isinstance(v, (str, bytes)):
            ev = v.encode() if isinstance(v, str) else v
            parts.append(encode_varint((k << 3) | 2) + encode_varint(len(ev)) + ev)
    return b''.join(parts)


def encode_protobuf_dict(fields: dict) -> bytes:
    return build_proto(fields)


def encrypt_api(plain_hex):
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(
        pad(bytes.fromhex(plain_hex), AES.block_size)
    ).hex()


def encrypt_api_bytes(plain_bytes):
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(
        pad(plain_bytes, AES.block_size)
    )


# ==================== build_majorlogin_payload ====================
async def build_majorlogin_payload(open_id, access_token, platform, client_version, device_info):
    try:
        payload_dict = {
            3:   str(datetime.now())[:-7],
            4:   "free fire",
            5:   1,
            7:   str(client_version) if client_version else "1.132.1",
            8:   "Android OS 10 / API-29 (QP1A.190711.020/V12.0.26.0.QCDINXM)",
            9:   "Handheld",
            10:  "Ncell",
            11:  "WIFI",
            12:  1600,
            13:  720,
            14:  "320",
            15:  "ARMv7 VFPv3 NEON | 2001 | 8",
            16:  3790,
            17:  "PowerVR Rogue GE8320",
            18:  "OpenGL ES 3.2 build 1.11@5425693",
            19:  "Google|00000000-0000-0000-0000-000000000000",
            20:  "111.119.38.133",
            21:  "en",
            22:  str(open_id),
            23:  "4",
            24:  "Handheld",
            25:  "Xiaomi M2006C3LII",
            26:  "ind",
            29:  str(access_token),
            30:  1,
            41:  "Ncell",
            42:  "WIFI",
            57:  "1ac4b80ecf0478a44203bf8fac6120f5",
            60:  53041,
            61:  7291,
            62:  2176,
            64:  7395,
            65:  53041,
            66:  7395,
            67:  53041,
            70:  4,
            73:  2,
            74:  "/data/app/com.dts.freefireth-yAPXAhp2RyIlrtNAM0VzKQ==/lib/arm",
            76:  1,
            77:  "066a589fa3f5658377634fe7b1d88556|/data/app/com.dts.freefireth-yAPXAhp2RyIlrtNAM0VzKQ==/base.apk",
            78:  6,
            79:  1,
            81:  "32",
            83:  "2019121227",
            85:  3,
            86:  "OpenGLES2",
            87:  3071,
            88:  4,
            89:  9329,
            93:  "3rd_party",
            94:  "KqsHT3r+fXQIu/dyZrEa8fJBhbJ5uqDES7YsAUfu+Mck9A+Bly6lFfYk7Q7Nj68pqI8I3g4Oz3gLxWef6Eh/jKyzHug=",
            95:  111207,
            96:  '{"cur_rate":null,"support_etc2":false}',
            97:  1,
            98:  "4",
            99:  "4",
            102: bytes.fromhex("42 54 4c 10 53 0e 5b 04 30"),
            104: 47591,
            105: 1,
            106: "https://dl-bs.ggpolarbear.com/live/ABHotUpdates/|https://core-bs.ggpolarbear.com/live/ABHotUpdates/|1c2462939e53942fc995400436a3dc7b",
            107: "c8e41b7a93f02d56e1a94c7b8203f5d1",
        }
        raw_proto = build_proto(payload_dict)
        encrypted = encrypt_api_bytes(raw_proto)
        return encrypted
    except Exception as e:
        print_error(f"[MAJORLOGIN_BUILD] Payload creation failed: {e}")
        traceback.print_exc()
        return None


async def send_majorlogin(data, release_version, server_url):
    try:
        url = f"{server_url}MajorLogin" if server_url.endswith('/') else f"{server_url}/MajorLogin"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = str(release_version)
        response = await client.post(url, headers=req_headers, data=data)
        if response.status_code != 200:
            print_error(f"[MAJORLOGIN] Server rejected payload with status {response.status_code}")
            return None
        response_content = response.content
        if len(response_content) < 40:
            return None
        res_proto = thunderFF_pb2.MajorLoginRes()
        try:
            res_proto.ParseFromString(response_content)
            if res_proto.region and res_proto.token:
                return res_proto
        except Exception:
            pass
        if len(response_content) > 64:
            try:
                res_proto = thunderFF_pb2.MajorLoginRes()
                res_proto.ParseFromString(response_content[64:])
                if res_proto.region and res_proto.token:
                    return res_proto
            except Exception:
                pass
        for offset in range(min(128, len(response_content))):
            try:
                candidate = thunderFF_pb2.MajorLoginRes()
                candidate.ParseFromString(response_content[offset:])
                if candidate.region and candidate.token:
                    return candidate
            except Exception:
                pass
        res_proto = thunderFF_pb2.MajorLoginRes()
        res_proto.ParseFromString(response_content)
        return res_proto
    except Exception as e:
        print_error(f"[MAJORLOGIN] Connection error: {e}")
        return None


async def send_getlogin(data, base_url, token, release_version):
    try:
        url = f"{base_url.rstrip('/')}/GetLoginData"
        req_headers = headers.copy()
        req_headers["ReleaseVersion"] = release_version
        req_headers['Authorization'] = f"Bearer {token}"
        req_headers['Host'] = "clientbp.ppmainecoonghj.com"
        response = await client.post(url, headers=req_headers, data=data)
        if response.status_code != 200:
            return None
        response_content = response.content

        res_proto = thunderFF_pb2.GetLoginDataRes()
        parsed_successfully = False
        try:
            res_proto.ParseFromString(response_content)
            if res_proto.functional_addrs or res_proto.informational_addrs:
                parsed_successfully = True
        except Exception:
            pass

        if not parsed_successfully:
            for offset in range(min(128, len(response_content))):
                try:
                    candidate = thunderFF_pb2.GetLoginDataRes()
                    candidate.ParseFromString(response_content[offset:])
                    if candidate.functional_addrs or candidate.informational_addrs:
                        res_proto = candidate
                        break
                except Exception:
                    pass

        dict_res = {}
        try:
            parsed = Parser().parse(response_content.hex())
            dict_res = await parse_results(parsed)
        except Exception:
            pass

        return res_proto, dict_res
    except Exception as e:
        return None


async def build_tcp_startup_packet(account_id, token, server_time, key, iv, region="ID", typ='OnLine'):
    uid_hex = f"{int(account_id):016x}"
    timestamp_hex = f"{int(server_time):08x}"
    encode_token = token.encode()
    encrypted_packet = (await aes_encrypt(encode_token, key, iv)).hex()
    encrypted_packet_length = f"{len(encrypted_packet) // 2:08x}"
    reg = str(region).upper() if region else "ID"
    if typ == 'OnLine':
        prefix = '7219' if reg == 'ID' else ('7214' if reg == 'IND' else '7215')
        return f"{prefix}{uid_hex}{timestamp_hex}00000000{encrypted_packet_length}{encrypted_packet}"
    else:
        prefix = '8119' if reg == 'ID' else ('8114' if reg == 'IND' else '8115')
        return f"{prefix}{uid_hex}{timestamp_hex}{encrypted_packet_length}{encrypted_packet}"

async def send_keep_alive(region="ID"):
    try:
        reg = str(region).upper() if region else "ID"
        ka_hex = "0219" if reg == "ID" else ("0214" if reg == "IND" else "0215")
        return bytes.fromhex(ka_hex)
    except Exception:
        return bytes.fromhex("0219")


# ==================== SQUAD HELPERS ====================
async def EnC_PacKeT(Pk, K, V):
    try:
        raw = bytes.fromhex(Pk)
        enc = AES.new(K, AES.MODE_CBC, V).encrypt(pad(raw, AES.block_size))
        return enc.hex()
    except Exception as e:
        print_error(f"[EnC_PacKeT] {e}")
        return ""


async def DecodE_HeX(H):
    R = hex(H)
    F = str(R)[2:]
    if len(F) == 1:
        F = "0" + F
        return F
    else:
        return F


async def GeneRaTePk(Pk, N, K, V):
    PkEnc = await EnC_PacKeT(Pk, K, V)
    _ = await DecodE_HeX(int(len(PkEnc) // 2))
    if len(_) == 2:
        HeadEr = N + "000000"
    elif len(_) == 3:
        HeadEr = N + "00000"
    elif len(_) == 4:
        HeadEr = N + "0000"
    elif len(_) == 5:
        HeadEr = N + "000"
    else:
        print('ErroR => GeneRatinG ThE PacKeT !! ')
        return b""
    return bytes.fromhex(HeadEr + _ + PkEnc)


async def CrEaTe_ProTo(fields: dict) -> bytes:
    return build_proto(fields)


# ==================== SQUAD PACKET BUILDERS ====================
def build_open_squad_payload(account_region: str, client_version: str, team_capacity: int = 4) -> bytes:
    reg = str(account_region).upper() if account_region else "ID"
    fields = {}
    fields[1] = 1

    fields[2] = {}
    fields[2][2] = bytes([11])
    fields[2][3] = 1
    fields[2][4] = int(team_capacity - 1)
    fields[2][5] = reg.lower()
    fields[2][8] = {1: "IDC1", 2: 48, 3: reg}
    fields[2][9] = 2
    fields[2][10] = bytes([1, 9, 10, 11, 18, 25, 32, 39])
    fields[2][11] = 1
    fields[2][13] = 1

    fields[2][14] = {}
    fields[2][14][1] = bytes.fromhex(
        "080280006467A4C3020100000000000400050001000000004C3324180F0000004676"
        "251400000000000000000000000000000000000000ff00000000cacfa16d"
    )
    fields[2][14][2] = 93
    fields[2][14][3] = (
        "p\\XT\u0013\u0002\tH\u0002\u0003\u0001\u000fS\u0005\u0002\u0002\u0004\u000f"
        "\u0004\u0002Q\b\u000f\u0002W\u0006\u0001VX\u0004TQ]P\u0003VR\u0001\u0007["
        "\u0017\u0006\u0002EsXDEI\u001b\t\u0018\u0006\u001a\u001a\u0007\u0007H\u001a"
        "BcsXPC}w[RHepI\u001f^\\A\u0003WCBj_he\n\u0010\bJ\u001e\u0001^w\u001ck\u0005"
        "Ez\fUbCcr\u001cTQEFCAWg\u0001\u0001\u0007\u0004\u0017\u0006\u0000EgrAhei~"
        "[gQcDA}_\bDqC\u0003\u0004cZmse\u000e\u001a\fK\\An\f{G\tq\u000bafX_^\u0002"
        "qX@ZSNF{Cc^G\f\u0013\u0001Ex[krOe\\}\u0006\u0000QCRZC\u0019lXA\u0005Avv\u0003"
        "\u0003p{\b\u0017\bLF\u000fSeIsUzq\u0001SwJY\u0000r~S{Na]|XVT~\u0004\u0015"
        "\u0004\fM\\\u0007q}}gLa\u0001{\u0006RXXsuVJZr\u0005BU\u0002n\u0004D\u000e\u001a"
        "\u0005Iw\u001e|UsL||qBYuittXMrbm^tuguv^\t\u0014\u0007E\u0007y\u001bg[cs{NDm~c"
        "\\W|~O\u001fD\u0001{\u0006l\u001bh_\u000e\u001a\u0006\u0007O\rd\u0005\fped~"
        "aDr}bvz\u007fC\u0003xBezC\u0002_]x\r\u0010\fHU\u0002vDVsuCZh\u0000tTcLX\u0001"
        "dEtwBf\t\u0003p\t"
    )
    fields[2][14][4] = "wY[Q"
    fields[2][14][6] = 11
    fields[2][14][7] = {3: 2071688288}
    fields[2][14][8] = str(client_version)
    fields[2][14][9] = 3
    fields[2][14][10] = 2
    fields[2][14][11] = (
        "\u0003bbSQ61JJBA4FAdV2KyoVlW39FjLPYC+QTWlQzE6kzmAk37hk/Va7/dNorNdc1eHg2"
        "11Am98XSECZ0RYZxpWRRtGDQ/1nAcmsWIwu18IPWzwlEfZzZuQE47NiJwi198nygyf5T8NF"
        "0OL4csXLqyck5SHMRJrAZkxJs/c31i42BbSk21eOYArT1cYT6BUNKpWUA8687K8Za9Cnn89"
        "MydzMiKKC6ag7ozUK8XHdtpLB0cBNBkrojGLY2rTljHVpTUILrYM0mcPW3fWHT/+4c23m8o"
        "wsCxfWtub2p0Oh9/PsXBy6Pp5RmZe5OM0mmAYaHb0cHPp924/gfUMH3X/pEGe0ykK3N5i7A"
        "vkOdIymfoV//W8Nah3fxtmCsK5mipYz4Vj3VRB4p+/vF/S0hxelTqwoQAzxicLPEYnEpCvc"
        "cFTTdx/iSqkBn6AH+yUqlH8Y9aGSkiuu5SXHn7uIi1bFrANOgHNuy5tJjl024kovRsrLT5L"
        "lvimlmioUGMzYCSDncSmB3D5PnkhdBsmkNZyYJWYtOYS9TAgP8JSNUzL7AURIbnnj8XrWlPe"
        "YxN/oJqEjI2Tqjd5R7klw1YCoBsff/K9aMOsj8lFZtgvgAXhEbH4RFlQZ"
    )

    fields[2][19] = 329
    fields[2][21] = "7OR\u0019"
    fields[2][24] = [
        {1: 3, 2: 391},
        {1: 4, 2: 385},
        {1: 5, 2: 192},
        {1: 29, 2: 204},
        {1: 22, 2: 120},
        {1: 14, 2: 175},
        {1: 21},
    ]
    fields[2][27] = "a_2504800200314510578"
    return encode_protobuf_dict(fields)


def build_join_squad_payload(account_region: str, client_version: str, team_code: str) -> bytes:
    reg = str(account_region).upper() if account_region else "ID"
    fields = {}
    fields[1] = 4
    fields[2] = {
        4: bytes([1, 7, 9, 10, 11, 18, 25, 32, 39]),
        5: str(team_code).strip(),
        6: 6,
        8: 1,
        9: {
            1: bytes.fromhex(
                "08FFF3BE903F27DF0203110111110000006B0003006800169194106F13CF106E"
                "4676251411010404dfe9e8b5ca3ca4f96a3119c00000004f03060301cacfa16d"
            ),
            2: 130,
            3: (
                "tY_S\u0013\b\u0001M\u0002\u0000T\u0000\u0005\u000e\b\t\u0002\u0000\u0003"
                "U\u0003\u0001V\u000fR\u000e\tRQ\u0002\u0004\u0005US\u0003XS\u0005\u0001"
                "\u0002\u0011\u0001\u0002JuTAEN\u001e\u0002\u001c\u0002\u001f\u0013\b\u0003"
                "M\u001cDbz_Q@}p_QOgsC\u001dYVI\u0004UAAj_ga\u0004\u0012\u0000K\u001d\u0007"
                "_t\u0019b\bCx\u0002UeGat\u001fTTCBLER\u0006\u0001\r\f\u0012\u0006\u0005"
                "N^^\u0002acH~r\u0004_\u0003RO|\u000bcd_@~Rnqrgh\n\u0015\nL\\Nh\u0000G"
                "\u000et\u0000eb]VQ\u0006t^F[ZIGxCdZD\u000b\u0011\u0002OALP@\u000fcSDy"
                "ATQApaT^{|wa]\\u0001E\u000f\u0013\nJfxve}\u0004J\u0003c{YL\u0007^^yDY"
                "Qf\bhe\u0005_wg\r\u0010\u0007\bEHpX@||\u0002Z\u0015TGyA\u0001JP\u0005"
                "tVkTE\u0000J\\u00b\u0003\nMr\u0018zTzK}qE]vnvwROuheYvwduvQ\r\u001a\u0005"
                "M\u0006z\u001dfXfzvHFcdXUz}O\u001aB\u0005t\u0002i\u001co_\u0004\u0012"
                "\u0003\u0007J\u0006b\u0003\u000eqlfvbEstgu~wB\u0001v@`yI\u0002ZPx\f\u0014"
                "\u0003NR\u0002yBZvuD_cd\u0004q]lH]\u0007bD}pCe\t\u0004t\n"
            ),
            4: "w^_R",
            6: 11,
            7: "\u0014\u0004aqrg\u0015\u0013",
            8: str(client_version),
            9: 3,
            10: 2,
            11: (
                "\u0003bbSQ6wxegh9qAdV2KyoVleA17yPmYF+yTnOrl+JMknmppeUBe5ZsiHueP2mZZ4"
                "KOs6b2Ail1S9z3qIeU9hGFZ2M6PP39/zjIc/RFVPAkgDagySswMVmYaPhkzU5IqNPow28"
                "43fyQUz9xI10NdMhl1WiI4Y6wCXBotiUS9wSgujQ4j0fWXUyklCxBWo8r27hyoGSVrPdTP"
                "XFMnJJpPRRFFmWqc3fWvMg+BNfxSRJOZRSrkzG0nNvSIJ4uZB2pqAlHIPEYx7bI6zsgwUV"
                "DiLZJKTUTyuCGbOd1DegDUFfazFesTG1LJknT5WhgzCsrBIy+f2l+LeJe5DW7wEwNaHam"
                "bM7ECXcJcLIhB9kJJsW0tFvXkk3HSdcQ8N1K6wjSKWhpkW0kV8Zrjj0jkhS/7AZ6T9GJR"
                "IA827YDtTorBvbx1UkXjYrqYI1nSa2GaMexGnqlurc5DE3v1R+mUBI9GqmjEPgTSYVBxyeC"
                "dQMHaMXGtspAhvkiO84ToU87sP45pylDEfFOVoc/rcmdzWeqlYPsv6txKRtIHcb0cO+MoV"
                "ShoU8ZUVRDF3znbqzVrscPIfplBaa79lwvQqzRubLl9XY="
            ),
        },
        11: {1: "IDC4", 2: 281, 3: reg},
        13: reg.lower(),
        16: "7OR\u0019",
        20: "\b\u0015",
        27: "\bH\u0010\u0003",
    }
    return encode_protobuf_dict(fields)


def build_invite_squad_payload(account_region: str, target_user_id: int, invite_type: int = 1) -> bytes:
    reg = str(account_region).upper() if account_region else "ID"
    fields = {}
    fields[1] = 2
    fields[2] = {
        1: int(target_user_id),
        2: reg,
        4: int(invite_type),
    }
    return encode_protobuf_dict(fields)


def _squad_packet_prefix(region: str) -> str:
    reg = (region or "ID").lower()
    if reg == "ind":
        return "0514"
    if reg == "id":
        return "0519"
    return "0515"


async def CreateNewSquad(K, V, region, client_version="1.132.8"):
    payload = build_open_squad_payload(region, client_version, team_capacity=SQUAD_MAX_MEMBERS)
    return await GeneRaTePk(payload.hex(), _squad_packet_prefix(region), K, V)


async def JoinSquad(team_code, K, V, region, client_version="1.132.8"):
    payload = build_join_squad_payload(region, client_version, team_code)
    return await GeneRaTePk(payload.hex(), _squad_packet_prefix(region), K, V)


async def SendInvite(target_uid, K, V, region, invite_type: int = 1):
    payload = build_invite_squad_payload(region, int(target_uid), invite_type=invite_type)
    return await GeneRaTePk(payload.hex(), _squad_packet_prefix(region), K, V)


async def accept_squad_invite(group_id, joiner_account_id, invitee_type, key, iv, region="id"):
    fields = {
        1: 38,
        2: {
            1: int(group_id),
            2: int(joiner_account_id),
            8: int(invitee_type),
        },
    }
    proto_hex = (await CrEaTe_ProTo(fields)).hex()
    return await GeneRaTePk(proto_hex, _squad_packet_prefix(region), key, iv)


async def detect_squad_invite(hex_data: str):
    try:
        if not hex_data or len(hex_data) < 40:
            return None
        prefixes = ("0515", "0516", "0517", "0a", "0d", "0f", "10")
        if not any(hex_data.startswith(p) for p in prefixes):
            return None
        raw = bytes.fromhex(hex_data)
        try:
            parsed = Parser().parse(raw[2:])
            d = await parse_results(parsed)
            code = get_proto_field(d, 10, None) or get_proto_field(d, 6, None)
            inviter = get_proto_field(d, 1, None) or get_proto_field(d, 2, None)
            if code and inviter:
                try:
                    code_s = str(code)
                    inviter_i = int(inviter) if not isinstance(inviter, int) else inviter
                    if code_s.isdigit() and 4 <= len(code_s) <= 12:
                        return (code_s, inviter_i)
                except Exception:
                    pass
        except Exception:
            pass
    except Exception:
        pass
    return None


# ==================== SQUAD ORCHESTRATOR (BR-ONLY) ====================
class SquadManager:
    def __init__(self):
        self.squad_writers: Dict[str, Any] = {}
        self.squad_accounts: Dict[str, Dict] = {}
        self.current_leader: Optional[str] = None
        self.squad_members: List[str] = []
        self.last_squad_time: float = 0.0
        self.squad_lock = asyncio.Lock()

        self.squad_role: Dict[str, str] = {}
        self.squad_member_uids: set = set()
        self.squad_ready_event: asyncio.Event = asyncio.Event()
        self.squad_filling: bool = False
        self.current_team_code: Optional[str] = None

    def register(self, uid: str, writer, account_data: Dict):
        uid = str(uid)
        self.squad_writers[uid] = writer
        self.squad_accounts[uid] = account_data

    def unregister(self, uid: str):
        uid = str(uid)
        self.squad_writers.pop(uid, None)
        self.squad_accounts.pop(uid, None)

        role = self.squad_role.pop(uid, None)
        self.squad_member_uids.discard(uid)

        if role == "leader" or self.current_leader == uid:
            self.current_leader = None
            self.squad_members.clear()
            self.squad_member_uids.clear()
            self.squad_role.clear()
            self.current_team_code = None
            self._mark_squad_ready()
        elif role == "member":
            try:
                self.squad_members.remove(str(uid))
            except ValueError:
                pass

    def role_of(self, uid: str) -> Optional[str]:
        return self.squad_role.get(str(uid))

    def can_start_match(self, uid: str) -> bool:
        role = self.squad_role.get(str(uid))
        return role is None or role == "leader"

    def is_squad_member(self, uid: str) -> bool:
        return self.squad_role.get(str(uid)) == "member"

    def _reset_squad_ready(self):
        self.squad_ready_event = asyncio.Event()

    def _mark_squad_ready(self):
        try:
            self.squad_ready_event.set()
        except Exception:
            pass

    async def wait_squad_ready(self, uid: str, timeout: float = 45.0) -> bool:
        try:
            await asyncio.wait_for(self.squad_ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def _send_squad_packet(self, uid: str, packet_data, label: str = "squad") -> bool:
        writer = self.squad_writers.get(str(uid))
        if not writer or writer.is_closing():
            print_warning(f"[SQUAD] Writer unavailable for {uid} ({label})")
            return False
        try:
            if isinstance(packet_data, (bytes, bytearray)):
                raw = bytes(packet_data)
            elif isinstance(packet_data, str):
                raw = bytes.fromhex(packet_data)
            else:
                print_error(f"[SQUAD] Invalid packet type for {label}: {type(packet_data)}")
                return False
            if not raw:
                print_error(f"[SQUAD] Empty packet for {label} (uid={uid})")
                return False
            writer.write(raw)
            await writer.drain()
            print_success(f"[SQUAD] {label} sent | uid={uid} | {len(raw)}B")
            return True
        except Exception as e:
            print_error(f"[SQUAD] Failed to send {label} for {uid}: {e}")
            return False

    async def create_squad(self, leader_uid: str) -> bool:
        acc = self.squad_accounts.get(str(leader_uid))
        if not acc:
            return False
        K = acc['aes_ak']
        V = acc['iv_i']
        region = acc.get('region', 'ID')
        client_version = acc.get('client_version', '1.132.8')
        try:
            pkt = await CreateNewSquad(K, V, region, client_version=client_version)
            ok = await self._send_squad_packet(leader_uid, pkt, "CreateSquad")
            if ok:
                self.current_leader = str(leader_uid)
                self.squad_members = [str(leader_uid)]
                self.squad_role[str(leader_uid)] = "leader"
                print_success(f"[SQUAD] Squad created by leader UID {leader_uid}")
            return ok
        except Exception as e:
            print_error(f"[SQUAD] CreateNewSquad failed: {e}")
            return False

    async def join_squad(self, member_uid: str, team_code: str) -> bool:
        acc = self.squad_accounts.get(str(member_uid))
        if not acc:
            print_warning(f"[SQUAD] join_squad: no account data for {member_uid}")
            return False
        K = acc['aes_ak']
        V = acc['iv_i']
        region = acc.get('region', 'ID')
        client_version = acc.get('client_version', '1.132.8')
        try:
            pkt = await JoinSquad(team_code, K, V, region, client_version=client_version)
            ok = await self._send_squad_packet(member_uid, pkt, f"JoinSquad({team_code})")
            if ok:
                self.squad_role[str(member_uid)] = "member"
                self.squad_member_uids.add(str(member_uid))
            return ok
        except Exception as e:
            print_error(f"[SQUAD] JoinSquad failed: {e}")
            return False

    async def invite_member(self, leader_uid: str, target_uid: str) -> bool:
        acc = self.squad_accounts.get(str(leader_uid))
        if not acc:
            return False
        K = acc['aes_ak']
        V = acc['iv_i']
        region = acc.get('region', 'ID')
        for attempt in range(SQUAD_INVITE_RETRY):
            try:
                pkt = await SendInvite(target_uid, K, V, region, invite_type=1)
                ok = await self._send_squad_packet(leader_uid, pkt, f"Invite->{target_uid}")
                if ok:
                    return True
            except Exception as e:
                print_warning(f"[SQUAD] Invite attempt {attempt + 1} error: {e}")
            await asyncio.sleep(1.0)
        return False

    async def accept_invite(self, member_uid: str, leader_uid: str,
                            group_id: Optional[int] = None,
                            invitee_type: int = 1) -> bool:
        acc = self.squad_accounts.get(str(member_uid))
        if not acc:
            print_warning(f"[SQUAD] accept_invite: no account data for {member_uid}")
            return False
        K = acc['aes_ak']
        V = acc['iv_i']
        region = acc.get('region', 'ID')

        gid = int(group_id) if group_id is not None else int(leader_uid)
        try:
            pkt = await accept_squad_invite(
                group_id=gid,
                joiner_account_id=int(member_uid),
                invitee_type=int(invitee_type),
                key=K,
                iv=V,
                region=region,
            )
            label = f"AcceptInvite(gid={gid}, join={member_uid})"
            ok = await self._send_squad_packet(member_uid, pkt, label)
            if ok:
                self.squad_role[str(member_uid)] = "member"
                self.squad_member_uids.add(str(member_uid))
            return ok
        except Exception as e:
            print_error(f"[SQUAD] AcceptInvite failed for {member_uid}: {e}")
            return False

    async def auto_fill(self):
        async with self.squad_lock:
            online = [u for u, w in self.squad_writers.items()
                      if w and not w.is_closing()]
            if len(online) < 2:
                self._mark_squad_ready()
                return False

            leader = online[0]
            members = online[1:SQUAD_MAX_MEMBERS]

            self._reset_squad_ready()
            self.squad_filling = True
            self.squad_member_uids.clear()
            self.squad_role[str(leader)] = "leader"

            print_info(f"[SQUAD] Auto-fill: leader={leader}, members={members}")
            try:
                if SQUAD_AUTO_CREATE:
                    if not await self.create_squad(leader):
                        self._mark_squad_ready()
                        return False
                    await asyncio.sleep(SQUAD_FILL_DELAY)
                else:
                    self.current_leader = str(leader)
                    self.squad_members = [str(leader)]
                    print_info(f"[SQUAD] Auto-create DISABLED; leader={leader} assigned as role=leader")

                for m in members:
                    if SQUAD_AUTO_INVITE:
                        ok_inv = await self.invite_member(leader, m)
                        await asyncio.sleep(SQUAD_ACCEPT_DELAY)
                        if ok_inv and SQUAD_AUTO_ACCEPT:
                            ok_acc = await self.accept_invite(
                                m, leader, group_id=int(leader), invitee_type=1
                            )
                            if ok_acc:
                                self.squad_members.append(str(m))
                                print_success(f"[SQUAD] Member {m} joined squad")
                        await asyncio.sleep(SQUAD_FILL_DELAY)
                    else:
                        self.squad_role[str(m)] = "member"
                        self.squad_members.append(str(m))

                print_success(
                    f"[SQUAD] Squad filled: {len(self.squad_members)}/{SQUAD_MAX_MEMBERS} members | leader={leader}"
                )
                self.last_squad_time = time.time()
                return True
            finally:
                self.squad_filling = False
                self._mark_squad_ready()


squad_manager = SquadManager()
_squad_loop_task: Optional[asyncio.Task] = None


def _account_level(uid: str) -> int:
    try:
        if uid in bot_state.accounts:
            return int(bot_state.accounts[uid].get('level', 1) or 1)
    except Exception:
        pass
    return 1


async def squad_orchestrator_loop():
    while True:
        try:
            if not SQUAD_MODE_ENABLED:
                await asyncio.sleep(10)
                continue

            eligible = []
            for uid, w in list(squad_manager.squad_writers.items()):
                if not w or w.is_closing():
                    squad_manager.unregister(uid)
                    continue
                lvl = _account_level(uid)
                if lvl < LONE_WOLF_UNLOCK_LEVEL:
                    eligible.append(uid)
                else:
                    if squad_manager.role_of(uid) is not None:
                        print_info(f"[SOLO] Lone Wolf — unregister dari squad | UID: {uid} | Lvl {lvl}")
                    squad_manager.unregister(uid)

            if len(eligible) < 2:
                await asyncio.sleep(30)
                continue

            if (time.time() - squad_manager.last_squad_time) > 60.0:
                try:
                    await squad_manager.auto_fill()
                except Exception as e:
                    print_warning(f"[SQUAD] auto_fill error: {e}")
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print_warning(f"[SQUAD] loop error: {e}")
            await asyncio.sleep(15)


# ==================== SEND START MATCH ====================
async def start_game_battle_royale(region, client_version, writer, key, iv):
    packet = bytes.fromhex("080112800a0a010110013a110a044944433110aa011a064555524f50453a100a044944433210311a064555524f504540014a0801090a0b1219202758016291090a8001303838463832424630324139363736373032303130313030303030303030303030303136303030313030313530303032323246393745454530463030303030303436373632353134303030303030303030303030303030303030303030303030303030303030303030303030303066663030303030303030636163666131366410241afb02735d5e571400024a775d45414d1a041b1c001f11010449715f4243481a001e1d071c1703004b1a4066785c524570735c51486775421b5c5a4c07504042685a63610816054e19025e75196001477c015165406370195f5547404e4550640103020f1304064863754268676c755f65576e40467e5f0a417a4701026d675d6e73670b1108495a4c6a0b78470b740065645e525a057258425f584a447d4e6759440c11044e7c596d7f4b625f7d04055a47505c4e1d6b5b4107447d7201057d7f0f14084e430457674f7e517d72015172415d027473577c4d615f79535256780911030f4d5e027a797f614165067806505d53777750475e75064257076500460817014e741e7e5078487e7a7c465e7669767153497064605a7376677773550d160148037e18675966787f4c42607a645f577e7b441b460776026b18685d0b110205490060020f70676175654674706671797f41067346677c4e06585e780f15074c57047b40517075415f6364027259674b5b0166407f7340600407770a22047a5d5c52300b3a0a167305067162727516134208312e3133302e3232480350015ae90403626253513635686e556f4e36416456324b796f566c636f477776484f624e56526c4d727073504b4f43654177616848494176795556497273743752737149734a7a786b3247525268377a2f637664626d504f6a73552f79626d38547a4c69586d2f474351696d494b53486833447955726f39515152756c34545350626d6d624b7949565937545671577059455372323646572f59624578507338514f706d317372785455736c30796a434144444d4f34616a654b615753366361496c554b4963797a494e396d52516f715277687939797257476d337a644345337a6a61436f492f5a585233656f65365a42647a64677654636b6b665733356e4d4c6a6a565072564b6433523172756174394e50514150724a5546627859696c4c5a3859707336654d5447666b6649793574666a526c314d4648706b51774c6373374439656378566c41636f374e664f6d2b30654756466c4434744478706771385533595973587645384842502f70666c767a737138316a32524f4d7857437556445442492f684735625462773166456e4249725162762b636144775147696f74554e316d4c4b77734379456f4766706746614251457645672b736a764c4c78704743334c304a5344532f74526169504354553344374e6249306547516651622f5a466f4c36455630775a324d6f583932414c572f5049752f56634663584e70596b356f7966326151416a536971486a2f363276354843644f525551303578754e6171795251625653704654303137655237675255636b4966366c6f447476342b514e4a4670766d74757077707774396a5a5974437a4b56743657726d6e36785837706658456251555434684f3758a201050803108703a201050804108103a20105080510c001a20105081d10cc01a2010408161078a20105080e10af01a201020815")
    proto = thunderFF_pb2.StartMatch()
    proto.ParseFromString(packet)
    if hasattr(proto.main, 'region_list') and len(proto.main.region_list) > 0:
        proto.main.region_list[0].region = region
        if len(proto.main.region_list) > 1:
            proto.main.region_list[1].region = region
    if hasattr(proto.main, 'client_version'):
        proto.main.client_version.remote_version = client_version
    packet = proto.SerializeToString()
    encrypted_packet = (await aes_encrypt(packet, key, iv)).hex()
    packet_length = len(encrypted_packet) // 2
    hex_length = hex(packet_length)[2:]
    hex_length = hex_length if len(hex_length) > 1 else "0" + hex_length
    reg = str(region).upper() if region else "ID"
    reg_prefix = "031500" if reg == "ID" else ("031400" if reg == "IND" else "031500")
    final_packet = reg_prefix + "0" * (6 - len(hex_length)) + hex_length + encrypted_packet
    writer.write(bytes.fromhex(final_packet))
    await writer.drain()
    print_info(f"[⚔] Battle Royale Match Search Packet Sent ({packet_length} bytes, prefix: {reg_prefix}) | Region: {reg}")


async def start_game_lone_wolf(region, client_version, writer, key, iv):
    packet = bytes.fromhex("080112800a0a010b102b3a110a044944433110aa011a064555524f50453a100a044944433210311a064555524f504540014a0801090a0b1219202758016291090a8001303838463832424630324139363736373032303130313030303030303030303030303136303030313030313530303032323246393745454530463030303030303436373632353134303030303030303030303030303030303030303030303030303030303030303030303030303066663030303030303030636163666131366410241afb02735d5e571400024a775d45414d1a041b1c001f11010449715f4243481a001e1d071c1703004b1a4066785c524570735c51486775421b5c5a4c07504042685a63610816054e19025e75196001477c015165406370195f5547404e4550640103020f1304064863754268676c755f65576e40467e5f0a417a4701026d675d6e73670b1108495a4c6a0b78470b740065645e525a057258425f584a447d4e6759440c11044e7c596d7f4b625f7d04055a47505c4e1d6b5b4107447d7201057d7f0f14084e430457674f7e517d72015172415d027473577c4d615f79535256780911030f4d5e027a797f614165067806505d53777750475e75064257076500460817014e741e7e5078487e7a7c465e7669767153497064605a7376677773550d160148037e18675966787f4c42607a645f577e7b441b460776026b18685d0b110205490060020f70676175654674706671797f41067346677c4e06585e780f15074c57047b40517075415f6364027259674b5b0166407f7340600407770a22047a5d5c52300b3a0a167305067162727516134208312e3133302e3232480350015ae90403626253513635686e556f4e36416456324b796f566c636f477776484f624e56526c4d727073504b4f43654177616848494176795556497273743752737149734a7a786b3247525268377a2f637664626d504f6a73552f79626d38547a4c69586d2f474351696d494b53486833447955726f39515152756c34545350626d6d624b7949565937545671577059455372323646572f59624578507338514f706d317372785455736c30796a434144444d4f34616a654b615753366361496c554b4963797a494e396d52516f715277687939797257476d337a644345337a6a61436f492f5a585233656f65365a42647a64677654636b6b665733356e4d4c6a6a565072564b6433523172756174394e50514150724a5546627859696c4c5a3859707336654d5447666b6649793574666a526c314d4648706b51774c6373374439656378566c41636f374e664f6d2b30654756466c4434744478706771385533595973587645384842502f70666c767a737138316a32524f4d7857437556445442492f684735625462773166456e4249725162762b636144775147696f74554e316d4c4b77734379456f4766706746614251457645672b736a764c4c78704743334c304a5344532f74526169504354553344374e6249306547516651622f5a466f4c36455630775a324d6f583932414c572f5049752f56634663584e70596b356f7966326151416a536971486a2f363276354843644f525551303578754e6171795251625653704654303137655237675255636b4966366c6f447476342b514e4a4670766d74757077707774396a5a5974437a4b56743657726d6e36785837706658456251555434684f3758a201050803108703a201050804108103a20105080510c001a20105081d10cc01a2010408161078a20105080e10af01a201020815")
    proto = ZyxTeam_pb2.StartMatch()
    proto.ParseFromString(packet)
    if hasattr(proto.main, 'region_list') and len(proto.main.region_list) > 0:
        proto.main.region_list[0].region = region
        if len(proto.main.region_list) > 1:
            proto.main.region_list[1].region = region
    if hasattr(proto.main, 'client_version'):
        proto.main.client_version.remote_version = client_version
    packet = proto.SerializeToString()
    encrypted_packet = (await aes_encrypt(packet, key, iv)).hex()
    packet_length = len(encrypted_packet) // 2
    hex_length = hex(packet_length)[2:]
    hex_length = hex_length if len(hex_length) > 1 else "0" + hex_length
    reg = str(region).upper() if region else "ID"
    reg_prefix = "031400"
    final_packet = reg_prefix + "0" * (6 - len(hex_length)) + hex_length + encrypted_packet
    writer.write(bytes.fromhex(final_packet))
    await writer.drain()
    print_info(f"[🐺] Lone Wolf Match Search Packet Sent ({packet_length} bytes, prefix: {reg_prefix}) | Region: {reg}")


# ==================== TEA & FRAME HELPERS ====================
async def has_ssan_zig(n):
    z = (n << 1) & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while z >= 0x80:
        out.append((z & 0x7F) | 0x80)
        z >>= 7
    out.append(z)
    return bytes(out)

async def uleb_encode(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)

async def tea_enc(v0, v1, k0, k1, k2, k3):
    s = 0
    for _ in range(_ROUNDS):
        s = (s + _DELTA) & 0xFFFFFFFF
        v0 = (v0 + (((((v1 << 4) & 0xFFFFFFFF) + k0) & 0xFFFFFFFF ^
                      ((v1 + s) & 0xFFFFFFFF) ^
                      (((v1 >> 5) + k1) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        v1 = (v1 + (((((v0 << 4) & 0xFFFFFFFF) + k2) & 0xFFFFFFFF ^
                      ((v0 + s) & 0xFFFFFFFF) ^
                      (((v0 >> 5) + k3) & 0xFFFFFFFF)))) & 0xFFFFFFFF
    return v0, v1

async def tea_dec(v0, v1, k0, k1, k2, k3):
    s = (_DELTA * _ROUNDS) & 0xFFFFFFFF
    for _ in range(_ROUNDS):
        v1 = (v1 - (((((v0 << 4) & 0xFFFFFFFF) + k2) & 0xFFFFFFFF ^
                      ((v0 + s) & 0xFFFFFFFF) ^
                      (((v0 >> 5) + k3) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        v0 = (v0 - (((((v1 << 4) & 0xFFFFFFFF) + k0) & 0xFFFFFFFF ^
                      ((v1 + s) & 0xFFFFFFFF) ^
                      (((v1 >> 5) + k1) & 0xFFFFFFFF)))) & 0xFFFFFFFF
        s = (s - _DELTA) & 0xFFFFFFFF
    return v0, v1

async def tea_cbc_encrypt(padded, key_bytes):
    k0, k1, k2, k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0, 4, 8, 12))
    out = bytearray(len(padded))
    prev_cipher = bytearray(8)
    prev_intermediate = bytearray(8)
    for i in range(0, len(padded), 8):
        xored = bytearray(8)
        for j in range(8):
            xored[j] = padded[i + j] ^ prev_cipher[j]
        e0, e1 = await tea_enc(
            struct.unpack_from("<I", xored, 0)[0],
            struct.unpack_from("<I", xored, 4)[0],
            k0, k1, k2, k3,
        )
        enc = bytearray(8)
        struct.pack_into("<I", enc, 0, e0)
        struct.pack_into("<I", enc, 4, e1)
        for j in range(8):
            out[i + j] = enc[j] ^ prev_intermediate[j]
        prev_cipher[:] = out[i:i + 8]
        prev_intermediate[:] = xored
    return bytes(out)

async def build_padded(content):
    pad_len = (8 - (len(content) + 10) % 8) % 8
    return bytes([pad_len, 0, 0]) + b"\x00" * pad_len + content + b"\x00" * 7

async def encode_header(layout, send_option, cmd, order_id, flags, length, k, v80):
    out = bytearray()
    for code in layout:
        value = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: length}[code]
        if _FIELD_SIZES[code] == 1:
            out.append((value & 0xFF) ^ k)
        else:
            v = ((value & 0xFFFF) ^ v80) & 0xFFFF
            out.append(v & 0xFF)
            out.append((v >> 8) & 0xFF)
    return bytes(out)

async def crc7_buff(crc, buf):
    c = crc & 0x7F
    for b in buf:
        c = CRC7_TABLE[((2 * (c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F

async def sv_frame(msg_key, layout, send_option, cmd, order_id, flags, content, key, encrypted=True):
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0]) + await encode_header(layout, send_option, cmd, order_id, flags, len(body), k, v80)
    packet = bytearray(hdr + body)
    packet[1] = await crc7_buff(0, bytes(packet[2:])) & 0x7F
    return bytes(packet)

async def build_match_startup_packets(token, udp_key, match_code, account_id, block_val,
                                      server_ip="", region="ID", client_version="1.132.8",
                                      client_version_code="2019121229", access_token="",
                                      mode_id=BR_MODE_ID, map_id=BR_MAP_ID):
    token = token.strip()
    udp_key = bytes.fromhex(udp_key)
    match_code = [int(ch) for ch in str(match_code).strip()]
    thunder_jwt = token[:660] if len(token) > 660 else token
    sharma_jwt = token[660:] if len(token) > 660 else ""
    encoded_thunder_jwt = thunder_jwt.encode() if isinstance(thunder_jwt, str) else thunder_jwt
    encoded_sharma_jwt = sharma_jwt.encode() if isinstance(sharma_jwt, str) else sharma_jwt
    garena420 = await has_ssan_zig(len(encoded_thunder_jwt)) + encoded_thunder_jwt
    reg = str(region).upper() if region else "ID"
    csoversea_block = bytes.fromhex(
        "ca0163736f7665727365612e7374726f6e67686f6c642e66726565666972656d6f62696c652e636f6d"
        "3b302e302e302e303b33342e3132362e37362e34353b33342e38372e3137372e31343b33342e38372e"
        "3137302e3233303b33352e3138352e3138332e35370000000000000100000000000000000000000001"
        "00000800000100000000000100a8a2d7bebd8d8bdf110200"
    )
    mid = bytes.fromhex('0000000001000102030101') + await has_ssan_zig(len(reg)) + reg.encode()
    mid += bytes.fromhex('0001030003000004')
    mid += await has_ssan_zig(len(client_version)) + client_version.encode()
    mid += await has_ssan_zig(len(client_version_code)) + client_version_code.encode()
    mid += csoversea_block
    clean_ip = server_ip.split(':')[0] if server_ip else "0.0.0.0"
    mid += await has_ssan_zig(len(clean_ip)) + clean_ip.encode()
    clean_acc_tok = access_token.strip() if access_token else ""
    if clean_acc_tok:
        mid += await has_ssan_zig(len(clean_acc_tok)) + clean_acc_tok.encode()
    mid += await has_ssan_zig(len(encoded_sharma_jwt)) + encoded_sharma_jwt
    tg_garena420 = (
        await uleb_encode(int(account_id)) +
        await uleb_encode(int(block_val)) +
        await uleb_encode(1) +
        await uleb_encode(int(mode_id)) +
        await uleb_encode(int(block_val)) +
        await uleb_encode(int(map_id)) +
        mid
    )
    process = await sv_frame(0x5E, match_code, 2, 447, 0, 1, garena420, udp_key)
    loading = await sv_frame(0x5A, match_code, 2, 448, 1, 1, tg_garena420, udp_key)
    return process.hex(), loading.hex()

async def produce_xor_key(secret_key):
    k = secret_key[0] if secret_key and len(secret_key) > 0 else 10
    return k, ((k << 8) | k) & 0xFFFF

async def parse_layout(layout):
    if isinstance(layout, str):
        return [int(ch) for ch in layout.strip()]
    return list(layout)

async def tea_cbc_decrypt(body, key_bytes):
    k0, k1, k2, k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0, 4, 8, 12))
    out = bytearray(len(body))
    prev_intermediate = bytearray(8)
    prev_cipher = bytearray(8)
    xored = bytearray(8)
    dec = bytearray(8)
    for i in range(0, len(body), 8):
        for j in range(8):
            xored[j] = body[i + j] ^ prev_intermediate[j]
        d0, d1 = await tea_dec(
            struct.unpack_from("<I", xored, 0)[0],
            struct.unpack_from("<I", xored, 4)[0],
            k0, k1, k2, k3
        )
        struct.pack_into("<I", dec, 0, d0)
        struct.pack_into("<I", dec, 4, d1)
        for j in range(8):
            out[i + j] = dec[j] ^ prev_cipher[j]
        prev_cipher[:] = body[i:i + 8]
        prev_intermediate[:] = dec
    return bytes(out)

async def build_hello_packet(text, key, layout):
    data = text.encode("utf-8")
    if len(data) > 25:
        raise ValueError(f"Text is too long ({len(data)} bytes)")
    content = b"\x10\x00\x00\x00" + data + b"\x00" * (29 - 4 - len(data))
    k, v80 = await produce_xor_key(key)
    layout = await parse_layout(layout)
    padded = await build_padded(content)
    enc_body = await tea_cbc_encrypt(padded, key)
    header_bytes = await encode_header(layout, 1, 1, 0, 1, len(enc_body), k, v80)
    packet = bytearray([0x63, 0x00]) + header_bytes + enc_body
    packet[1] = await crc7_buff(0, packet[2:]) & 0x7F
    return bytes(packet).hex()

async def classify(frame):
    cmd = frame["cmd"]
    msg_name = MESSAGE_ID_TO_NAME.get(cmd, f"UNKNOWN_{cmd}")
    if msg_name == "UDP_HELLO": return "HELLO"
    if msg_name == "UDP_ACK": return "ACK"
    if msg_name == "UDP_PING": return "PING"
    if msg_name == "RUDP_JOIN_MATCH": return "JOIN_MATCH"
    if msg_name.startswith("RUDP_"): return msg_name
    if msg_name.startswith("UDP_"): return msg_name
    return "DATA"

async def build_packet(msg_key, layout, send_option, cmd, order_id, flags, content, key, encrypted=True):
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0])
    for code in layout:
        value = {0: send_option, 1: cmd, 2: order_id, 3: flags, 4: len(body)}[code]
        if _FIELD_SIZES[code] == 1:
            hdr.append((value & 0xFF) ^ k)
        else:
            v = ((value & 0xFFFF) ^ v80) & 0xFFFF
            hdr.append(v & 0xFF)
            hdr.append((v >> 8) & 0xFF)
    packet = bytearray(hdr + body)
    packet[1] = await crc7_buff(0, bytes(packet[2:])) & 0x7F
    return bytes(packet)

async def layouts_from_mask(mask):
    ru = [int(c) for c in str(mask).strip()]
    nr = [c for c in ru if c != 2]
    return ru, nr

async def reply_for(frame, key, mask, ack_key=0x68, ping_key=0x6D, hello_key=0x5B, ack_style="short"):
    ru, nr = await layouts_from_mask(mask)
    typ = await classify(frame)
    if typ == "HELLO":
        if ack_style == "echo":
            content = frame["content"] if frame["content"] else b"\x10\x00\x00\x00"
            return typ, await build_packet(hello_key, nr, 1, 1, None, 1, content, key)
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x01\x00", key)
    if typ == "ACK":
        content = frame["content"] if frame["content"] else b"\x01\x00"
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, content, key)
    if typ == "PING":
        c = frame["content"]
        counter = c[:4] if len(c) >= 4 else c
        return typ, await build_packet(ping_key, nr, 0, 3, None, 0, counter + b"\x00\x00\x00", key, encrypted=False)
    if typ == "JOIN_MATCH":
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x02\x00", key)
    return typ, None

async def keepalive_ping(sock, ip, port, key_bytes, mask, stop_event):
    nr = (await layouts_from_mask(mask))[1]
    ping_keys = [0x66, 0x6D, 0x69, 0x6C, 0x6B, 0x6E, 0x6F, 0x70]
    loop = asyncio.get_event_loop()
    i = 0
    while not stop_event.is_set():
        pk = ping_keys[i % len(ping_keys)]
        counter = int(time.time() * 1000) & 0xFFFFFFFF
        pkt = await build_packet(pk, nr, 0, 3, None, 0, struct.pack("<I", counter) + b"\x00\x00\x00", key_bytes, encrypted=False)
        try:
            await loop.sock_sendto(sock, pkt, (ip, port))
        except Exception:
            pass
        i += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

async def try_header(buf, layout, k, v80):
    off = 2
    out = {}
    for code in layout:
        size = _FIELD_SIZES[code]
        if off + size > len(buf):
            return None
        out[_FIELD_NAMES[code]] = (buf[off] ^ k) if size == 1 else ((buf[off] | (buf[off + 1] << 8)) ^ v80) & 0xFFFF
        off += size
    out["headerLen"] = off
    return out

async def oicq_unpad(padded):
    if not padded or len(padded) < 8:
        return None
    if not all(padded[-1 - i] == 0 for i in range(7)):
        return None
    pad_len = padded[0] & 0x07
    s = 3 + pad_len
    e = len(padded) - 7
    return padded[s:e] if s < e else b""

async def decode_packet(packet, key, mask=None):
    data = bytes(packet) if isinstance(packet, bytes) else bytes.fromhex(packet)
    if len(data) < 8:
        return None
    k = key[0]
    v80 = ((k << 8) | k) & 0xFFFF
    crc_ok = (data[1] & 0x7F) == await crc7_buff(0, data[2:])
    candidates = []
    if mask:
        ru, nr = await layouts_from_mask(mask)
        layouts = [("RUDP", ru), ("nonRUDP", nr)]
    else:
        layouts = [("RUDP", list(p)) for p in itertools.permutations([0, 1, 2, 3, 4])]
        layouts += [("nonRUDP", list(p)) for p in itertools.permutations([0, 1, 3, 4])]
    for kind, layout in layouts:
        f = await try_header(data, layout, k, v80)
        if not f:
            continue
        if f["flags"] > 7 or f["sendOption"] > 7:
            continue
        if f["length"] != len(data) - f["headerLen"]:
            continue
        body = data[f["headerLen"]:f["headerLen"] + f["length"]]
        content = None
        padded = None
        if f["flags"] & 1:
            if len(body) < 8 or len(body) % 8 != 0:
                continue
            padded = await tea_cbc_decrypt(body, key)
            content = await oicq_unpad(padded)
            if content is None:
                continue
        else:
            content = body
        score = (1 if crc_ok else 0) + (1 if content is not None else 0)
        candidates.append({
            "kind": kind, "layout": layout, "headerLen": f["headerLen"],
            "msgKey": data[0], "cmd": f["cmd"], "flags": f["flags"],
            "sendOption": f["sendOption"], "orderId": f.get("orderId"),
            "length": f["length"], "content": content, "crcOk": crc_ok,
            "padded": padded, "score": score, "total": len(data),
        })
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c["kind"] == "RUDP" or c["kind"] == "nonRUDP", c["score"]), reverse=True)
    return candidates[0]


# ==================== ANTI-AFK ====================
async def send_anti_afk_movement(sock, server_addr, udp_key_bytes, match_code, account_id):
    try:
        nr = (await layouts_from_mask(match_code))[1]
        rand_x = random.randint(-100, 100)
        rand_y = random.randint(-50, 50)
        rand_z = random.randint(-100, 100)
        ts = int(time.time() * 1000) & 0xFFFFFFFF
        try:
            player_id = int(account_id)
        except Exception:
            player_id = 0
        body = struct.pack("<IIiiiH", player_id & 0xFFFFFFFF, ts, rand_x, rand_y, rand_z, 1)
        pkt = await build_packet(0x6B, nr, 0, 2001, None, 0, body, udp_key_bytes, encrypted=False)
        loop = asyncio.get_event_loop()
        await loop.sock_sendto(sock, pkt, server_addr)
        return True
    except Exception:
        return False


async def send_anti_afk_fire(sock, server_addr, udp_key_bytes, match_code, account_id):
    try:
        nr = (await layouts_from_mask(match_code))[1]
        try:
            player_id = int(account_id)
        except Exception:
            player_id = 0
        ts = int(time.time() * 1000) & 0xFFFFFFFF
        weapon_id = random.choice([101, 102, 103, 201, 202])
        fire_body = struct.pack("<IIHB", player_id & 0xFFFFFFFF, ts, weapon_id, 1)
        fire_pkt = await build_packet(0x68, nr, 0, 104, None, 0, fire_body, udp_key_bytes, encrypted=False)
        loop = asyncio.get_event_loop()
        await loop.sock_sendto(sock, fire_pkt, server_addr)
        await asyncio.sleep(random.uniform(0.15, 0.30))
        stop_body = struct.pack("<IIHB", player_id & 0xFFFFFFFF, ts + 200, weapon_id, 0)
        stop_pkt = await build_packet(0x68, nr, 0, 105, None, 0, stop_body, udp_key_bytes, encrypted=False)
        await loop.sock_sendto(sock, stop_pkt, server_addr)
        return True
    except Exception:
        return False


async def anti_afk_worker(sock, resolved_ip, port, udp_key_bytes, match_code,
                          account_id, stop_event, match_index):
    server_addr = (resolved_ip, port)
    fire_count = 0
    move_count = 0
    try:
        await asyncio.sleep(2.5)
        while not stop_event.is_set():
            try:
                roll = random.random()
                if roll < ANTI_AFK_FIRE_CHANCE:
                    ok = await send_anti_afk_fire(sock, server_addr, udp_key_bytes, match_code, account_id)
                    if ok:
                        fire_count += 1
                    if random.random() < 0.4:
                        await send_anti_afk_movement(sock, server_addr, udp_key_bytes, match_code, account_id)
                        move_count += 1
                else:
                    ok = await send_anti_afk_movement(sock, server_addr, udp_key_bytes, match_code, account_id)
                    if ok:
                        move_count += 1
                wait_time = random.uniform(ANTI_AFK_MIN_INTERVAL, ANTI_AFK_MAX_INTERVAL)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=wait_time)
                    break
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass
    finally:
        try:
            if fire_count + move_count > 0:
                print_info(f"[🎮] Anti-AFK #{match_index} | Fires: {fire_count} | Moves: {move_count} | UID: {str(account_id)}")
        except Exception:
            pass


# ==================== PLAY GAME (UDP MATCH) ====================
async def play_game(server_ip_port, thunder, sharma, udp_key, match_code,
                    account_id, player_region, client_version, key, iv,
                    match_index: int, on_match_complete=None):
    match_start_time = time.time()
    ping_task = None
    anti_afk_task = None
    sock = None
    ping_stop = asyncio.Event()
    anti_afk_stop = asyncio.Event()
    anti_afk_started = False
    uid_str = str(account_id)
    completed_cleanly = False

    try:
        ip, port = server_ip_port.split(":")
        port = int(port)
        resolved_ip = await resolve_host_cloudflare(ip)
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('0.0.0.0', 0))
        except Exception:
            pass
        optimize_udp_socket(sock)
        sock.setblocking(False)
        udp_key_bytes = bytes.fromhex(udp_key)
        hello_packet = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
        await loop.sock_sendto(sock, bytes.fromhex(hello_packet), (resolved_ip, port))
        ack_state = "waiting_for_hello_reply"
        thunder_sent = False
        sharma_sent = False
        join_match_received = False
        local_closed = False
        send_lock = asyncio.Lock()
        ping_task = asyncio.create_task(
            keepalive_ping(sock, resolved_ip, port, udp_key_bytes, match_code, ping_stop)
        )
        last_activity = time.time()
        MAX_IDLE_BEFORE_HELLO_RESEND = 7.0
        print_colored(
            f"🎮 [MATCH #{match_index}] UDP started → {server_ip_port} (DNS: {resolved_ip})",
            Colors.MAGENTA
        )

        async def start_anti_afk_once():
            nonlocal anti_afk_task, anti_afk_started
            if anti_afk_started:
                return
            anti_afk_started = True
            anti_afk_task = asyncio.create_task(
                anti_afk_worker(sock, resolved_ip, port, udp_key_bytes, match_code,
                                account_id, anti_afk_stop, match_index)
            )
            print_success(f"[🎮] Anti-AFK Worker Started | Match #{match_index}")

        async def send_thunder_sharma_inline():
            nonlocal ack_state, thunder_sent, sharma_sent
            if thunder_sent:
                return
            async with send_lock:
                if thunder_sent:
                    return
                try:
                    await loop.sock_sendto(sock, bytes.fromhex(thunder), (resolved_ip, port))
                    thunder_sent = True
                    await asyncio.sleep(0.1)
                    prepare_ack = await build_packet(
                        0x68, (await layouts_from_mask(match_code))[1],
                        0, 2, None, 1, b"\x01\x00", udp_key_bytes
                    )
                    await loop.sock_sendto(sock, prepare_ack, (resolved_ip, port))
                    await asyncio.sleep(0.2)
                    await loop.sock_sendto(sock, bytes.fromhex(sharma), (resolved_ip, port))
                    sharma_sent = True
                    ack_state = "thunder_sharma_sent"
                    print_success(f"[MATCH #{match_index}] Startup packets delivered!")
                    await start_anti_afk_once()
                except Exception as e:
                    print_error(f"[MATCH #{match_index}] send error: {e}")

        while not local_closed:
            if time.time() - match_start_time > MAX_MATCH_DURATION:
                break
            try:
                response, server_addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 65535), timeout=1.5)
                if response:
                    last_activity = time.time()
                    frame = await decode_packet(response, udp_key_bytes, match_code)
                    if frame:
                        ptype = await classify(frame)
                        if frame['cmd'] in [103, 107]:
                            print_success(f"[MATCH #{match_index}] Completed (cmd {frame['cmd']})")
                            completed_cleanly = True
                            local_closed = True
                            continue
                        if frame['cmd'] == 101:
                            try:
                                ack_pkt = await build_packet(
                                    0x68, (await layouts_from_mask(match_code))[1],
                                    0, 2, None, 1, b"\x01\x00", udp_key_bytes
                                )
                                await loop.sock_sendto(sock, ack_pkt, server_addr)
                            except Exception:
                                pass
                            continue
                        if ptype in ["ACK", "PING", "HELLO", "JOIN_MATCH"]:
                            if ptype == "HELLO" and ack_state == "waiting_for_hello_reply":
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code, ack_style="short")
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                                ack_state = "ack_sent_waiting"
                            elif ptype == "ACK":
                                if ack_state == "waiting_for_hello_reply":
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply:
                                        await loop.sock_sendto(sock, reply, server_addr)
                                    ack_state = "ready_to_send_thunder"
                                elif ack_state == "ack_sent_waiting":
                                    ack_state = "ready_to_send_thunder"
                                else:
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply:
                                        await loop.sock_sendto(sock, reply, server_addr)
                            elif ptype == "PING":
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                            elif ptype == "JOIN_MATCH" and not join_match_received:
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                                    join_match_received = True
            except asyncio.TimeoutError:
                if ack_state == "ready_to_send_thunder" and not thunder_sent:
                    await send_thunder_sharma_inline()
                elif ack_state == "waiting_for_hello_reply":
                    if (time.time() - last_activity) > MAX_IDLE_BEFORE_HELLO_RESEND:
                        try:
                            pkt = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
                            await loop.sock_sendto(sock, bytes.fromhex(pkt), (resolved_ip, port))
                        except Exception:
                            pass
                        last_activity = time.time()
                    if (time.time() - match_start_time) > 25.0:
                        print_warning(f"[MATCH #{match_index}] Handshake timeout")
                        break
                elif ack_state == "thunder_sharma_sent":
                    if (time.time() - last_activity) > MATCH_IDLE_TIMEOUT:
                        print_success(f"[MATCH #{match_index}] Finished naturally")
                        completed_cleanly = True
                        break
                continue
            except BlockingIOError:
                await asyncio.sleep(0.05)
            except OSError:
                await asyncio.sleep(0.5)
                continue
            except Exception:
                await asyncio.sleep(0.5)
                continue
            if ack_state == "ready_to_send_thunder" and not thunder_sent:
                await send_thunder_sharma_inline()
        return f"match #{match_index} finished"
    except Exception as e:
        print_error(f"[MATCH #{match_index}] error: {e}")
        return f"match #{match_index} error"
    finally:
        if completed_cleanly:
            try:
                bot_state.increment_match(uid_str)
                print_success(f"[★] Match #{match_index} Complete | UID: {uid_str}")
                if on_match_complete:
                    try:
                        asyncio.create_task(on_match_complete())
                    except Exception as e:
                        print_warning(f"[EXP-REFRESH] post-match trigger failed: {e}")
            except Exception:
                pass
        anti_afk_stop.set()
        if anti_afk_task:
            try:
                await asyncio.wait_for(anti_afk_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                anti_afk_task.cancel()
                try:
                    await anti_afk_task
                except Exception:
                    pass
        ping_stop.set()
        if ping_task:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        remaining = await _dec_match(uid_str)
        try:
            bot_state.update_status(uid_str, "ONLINE" if remaining == 0 else "IN_MATCH", remaining)
        except Exception:
            pass


# ==================== FUNCTIONAL WORKER ====================
async def functional_lone_wolf(addrs, starter_packet, account_region, client_version,
                                key, iv, account_id="", account_data=None,
                                account_level: int = 1, max_reconnects=10):
    reconnects = 0
    ip, port = addrs.split(":")
    play_matches: List[asyncio.Task] = []
    no_response_count = 0
    search_attempts = 0
    last_start_time = 0.0
    uid_str = str(account_id)
    current_level = int(account_level or 1)
    consecutive_parse_failures = 0
    current_token = starter_packet
    current_key = key
    current_iv = iv
    current_account_data = account_data

    async def trigger_post_match_refresh():
        await asyncio.sleep(EXP_REFRESH_AFTER_MATCH_DELAY)
        try:
            cd = bot_state.account_credentials.get(uid_str)
            if cd:
                await refresh_account_profile(cd)
        except Exception as e:
            print_warning(f"[EXP-REFRESH] post-match error: {e}")

    try:
        while True:
            if current_account_data:
                fresh = None
                if current_account_data.get('auth_type') == 'guest' and current_account_data.get('auth_uid'):
                    fresh = cache_get(str(current_account_data['auth_uid']))
                elif current_account_data.get('auth_type') == 'token' and current_account_data.get('auth_token'):
                    fresh = cache_get(f"tok_{current_account_data['auth_token'][:20]}")
                if fresh:
                    try:
                        lv = int(fresh.get('level', current_level) or current_level)
                        if lv != current_level:
                            print_info(f"[LEVEL] UID {uid_str}: {current_level} → {lv} (cache)")
                            current_level = lv
                    except Exception:
                        pass
            try:
                if uid_str in bot_state.accounts:
                    live_lv = int(bot_state.accounts[uid_str].get('level', current_level) or current_level)
                    if live_lv != current_level:
                        print_info(f"[LEVEL] UID {uid_str}: {current_level} → {live_lv} (live)")
                        current_level = live_lv
            except Exception:
                pass

            is_lone_wolf = (current_level >= LONE_WOLF_UNLOCK_LEVEL)

            writer = None
            gateway_ping_task = None
            try:
                resolved_ip = await resolve_host_cloudflare(ip)
                reader, writer = await asyncio.open_connection(resolved_ip, int(port))
                bot_state.register_writer(uid_str, writer)

                if current_account_data and not is_lone_wolf:
                    squad_manager.register(uid_str, writer, current_account_data)
                elif current_account_data and is_lone_wolf:
                    print_info(f"[SOLO] Lone Wolf — tidak didaftarkan ke squad | UID: {uid_str} | Lvl {current_level}")

                raw_sock = writer.get_extra_info('socket')
                if raw_sock:
                    optimize_tcp_socket(raw_sock)
                writer.write(bytes.fromhex(current_token))
                await writer.drain()

                try:
                    init_ka = await send_keep_alive(account_region)
                    if init_ka and writer and not writer.is_closing():
                        writer.write(init_ka)
                        await asyncio.wait_for(writer.drain(), timeout=3)
                except Exception:
                    pass

                async def func_gateway_keepalive():
                    ka_bytes = await send_keep_alive(account_region)
                    while True:
                        await asyncio.sleep(5)
                        try:
                            if writer and not writer.is_closing():
                                writer.write(ka_bytes)
                                await writer.drain()
                        except Exception:
                            break

                gateway_ping_task = asyncio.create_task(func_gateway_keepalive())

                print_success(f"[✓] TCP Gateway Connected | UID: {uid_str} | Lvl {current_level}")
                reconnects = 0
                no_response_count = 0
                last_start_time = 0.0

                async def send_start_match():
                    nonlocal search_attempts, last_start_time
                    search_attempts += 1
                    current_region = "ID"
                    if is_lone_wolf:
                        mode_tag = "LONE WOLF"
                        starter_func = start_game_lone_wolf
                    else:
                        mode_tag = "BATTLE ROYALE"
                        starter_func = start_game_battle_royale
                    try:
                        await asyncio.sleep(random.uniform(0.2, 0.4))
                        print_info(f"[{mode_tag}] Searching match... | UID: {uid_str} | Attempt #{search_attempts} | Lvl {current_level}")
                        await starter_func(
                            current_region, client_version, writer,
                            current_key, current_iv
                        )
                        active = await _get_match_count(uid_str)
                        try:
                            bot_state.update_status(uid_str, f"SEARCHING ({mode_tag})", active)
                        except Exception:
                            pass
                    except Exception as e:
                        print_warning(f"[!] StartMatch attempt notice: {e}")
                    last_start_time = asyncio.get_running_loop().time()

                if SQUAD_MODE_ENABLED and not is_lone_wolf:
                    print_info(f"[SQUAD] Awaiting orchestrator role assignment | UID: {uid_str}")
                    wait_role_deadline = time.time() + 15.0
                    while time.time() < wait_role_deadline:
                        role = squad_manager.role_of(uid_str)
                        if role is not None:
                            break
                        await asyncio.sleep(0.5)

                    role = squad_manager.role_of(uid_str)
                    if role == "leader":
                        print_info(f"[SQUAD] I am LEADER ({uid_str}) -> waiting for members to accept invites...")
                        got_ready = await squad_manager.wait_squad_ready(uid_str, timeout=45.0)
                        if got_ready:
                            joined = len(squad_manager.squad_member_uids)
                            print_success(f"[SQUAD] Squad ready ({joined}/{SQUAD_MAX_MEMBERS - 1} members joined)")
                        else:
                            print_warning(f"[SQUAD] Squad wait timeout -> starting solo matchmaking")
                        await send_start_match()
                    elif role == "member":
                        print_success(f"[SQUAD] I am MEMBER ({uid_str}) -> waiting for leader's StartMatch")
                        last_start_time = asyncio.get_running_loop().time()
                    else:
                        await send_start_match()
                else:
                    if is_lone_wolf:
                        print_info(f"[SOLO] Lone Wolf — squad gate dilewati | UID: {uid_str}")
                    await send_start_match()

                while True:
                    play_matches[:] = [m for m in play_matches if not m.done()]

                    if bot_state.is_paused(uid_str):
                        try:
                            bot_state.update_status(uid_str, "PAUSED", 0)
                        except Exception:
                            pass
                        bot_state.unregister_writer(uid_str, writer)
                        squad_manager.unregister(uid_str)
                        if gateway_ping_task:
                            gateway_ping_task.cancel()
                        await safe_close_writer(writer)
                        writer = None
                        while bot_state.is_paused(uid_str):
                            await asyncio.sleep(1.0)
                        break

                    now = asyncio.get_running_loop().time()
                    if len(play_matches) == 0 and (now - last_start_time >= START_MATCH_INTERVAL):
                        squad_ok = (
                            is_lone_wolf
                            or (not SQUAD_MODE_ENABLED)
                            or squad_manager.can_start_match(uid_str)
                        )
                        if squad_ok:
                            await send_start_match()
                        else:
                            last_start_time = now

                    try:
                        data = await asyncio.wait_for(reader.read(8192), timeout=0.5)
                    except asyncio.TimeoutError:
                        no_response_count += 1
                        if no_response_count > 60:
                            no_response_count = 0
                        continue

                    if not data:
                        raise ConnectionError("Connection closed by server")

                    hex_data = data.hex()
                    packet_length = len(data)
                    no_response_count = 0

                    if hex_data.startswith("0300") and 10 < packet_length < 30:
                        print_info(f"[🔍] Server Confirmed Match Queue | UID: {uid_str} | Size: {packet_length}B")
                        continue

                    if hex_data.startswith("0300") and packet_length >= 300:
                        mode_tag = "LONE_WOLF" if is_lone_wolf else "BATTLE_ROYALE"
                        print_success(f"[⚔] Match Found [{mode_tag}] | UID: {uid_str}")
                        try:
                            res = json.loads(await decode_protobuf(hex_data[10:]))
                            token = None
                            udp_key = None
                            match_code = None
                            server_ip_port = None
                            match_account_id = None
                            block_val = None
                            if '42' in res and 'data' in res['42']:
                                match_code = res['42']['data']
                            if '5' in res and 'data' in res['5']:
                                res_field5 = res['5']['data']
                                server_ip_port = res_field5.get('2', {}).get('data')
                                udp_key = res_field5.get('3', {}).get('data')
                                token = res_field5.get('4', {}).get('data')
                                if '42' in res_field5:
                                    match_code = res_field5['42']['data']
                            if '1' in res and 'data' in res['1']:
                                match_account_id = res['1']['data']
                            if '5' in res and 'data' in res['5']:
                                block_val = res['5']['data'].get('1', {}).get('data')
                            effective_acc_id = match_account_id or account_id or "BD_BOT"
                            if token and udp_key and match_code and server_ip_port:
                                acc_tok = current_account_data.get('access_token', '') if current_account_data else ""
                                if is_lone_wolf:
                                    selected_mode_id = LONE_WOLF_MODE_ID
                                    selected_map_id = LONE_WOLF_MAP_ID
                                else:
                                    selected_mode_id = BR_MODE_ID
                                    selected_map_id = BR_MAP_ID
                                print_info(f"[🎯] mode_id={selected_mode_id} map_id={selected_map_id} | UID: {uid_str}")
                                thunder, sharma = await build_match_startup_packets(
                                    token, udp_key, match_code, effective_acc_id, block_val or 0,
                                    server_ip=server_ip_port, region=account_region,
                                    client_version=client_version, access_token=acc_tok,
                                    mode_id=selected_mode_id, map_id=selected_map_id
                                )
                                match_index = await _inc_match(uid_str)
                                print_info(f"[⚔] Match #{match_index} Injected [{mode_tag}] -> {server_ip_port}")
                                try:
                                    bot_state.update_status(uid_str, f"IN_MATCH ({mode_tag})", 1)
                                except Exception:
                                    pass
                                new_match = asyncio.create_task(
                                    play_game(
                                        server_ip_port, thunder, sharma, udp_key, match_code,
                                        effective_acc_id, "ID", client_version,
                                        current_key, current_iv,
                                        match_index=match_index,
                                        on_match_complete=trigger_post_match_refresh
                                    )
                                )
                                play_matches.append(new_match)
                                consecutive_parse_failures = 0
                                async def drain_gateway_reader():
                                    while not new_match.done():
                                        try:
                                            _ = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                                        except asyncio.TimeoutError:
                                            continue
                                        except Exception:
                                            break
                                drain_task = asyncio.create_task(drain_gateway_reader())
                                try:
                                    await new_match
                                except Exception as e:
                                    print_error(f"[MATCH #{match_index}] Match error: {e}")
                                finally:
                                    drain_task.cancel()
                                    try:
                                        await drain_task
                                    except asyncio.CancelledError:
                                        pass
                                play_matches[:] = [m for m in play_matches if not m.done()]
                                if gateway_ping_task:
                                    gateway_ping_task.cancel()
                                bot_state.unregister_writer(uid_str, writer)
                                squad_manager.unregister(uid_str)
                                await safe_close_writer(writer)
                                writer = None
                                print_info(f"[OFFLINE] Match #{match_index} concluded. Next search in {NEW_MATCH_DELAY}s...")
                                await asyncio.sleep(NEW_MATCH_DELAY)
                                reconnects = 0
                                break
                            else:
                                print_info(f"[FUNCTIONAL] Config packet ({packet_length}B), maintaining queue...")
                                continue
                        except Exception as e:
                            print_warning(f"[FUNCTIONAL] Match packet notice: {e}, maintaining queue...")
                            continue

                    if 30 <= packet_length <= 40:
                        continue

            except asyncio.CancelledError:
                if gateway_ping_task:
                    gateway_ping_task.cancel()
                if writer:
                    bot_state.unregister_writer(uid_str, writer)
                    squad_manager.unregister(uid_str)
                raise
            except Exception as e:
                if gateway_ping_task:
                    gateway_ping_task.cancel()
                if writer:
                    bot_state.unregister_writer(uid_str, writer)
                    squad_manager.unregister(uid_str)
                play_matches[:] = [m for m in play_matches if not m.done()]
                await safe_close_writer(writer)
                if "Cache expired" in str(e):
                    print_warning(f"[!] Token expired for UID: {uid_str}. Refreshing...")
                    break
                reconnects += 1
                if reconnects > max_reconnects:
                    print_warning(f"[!] UID {uid_str} hit max reconnects ({max_reconnects}) -> refreshing token...")
                    if current_account_data:
                        try:
                            if current_account_data.get('auth_uid'):
                                cache_invalidate(str(current_account_data['auth_uid']))
                            if current_account_data.get('auth_token'):
                                cache_invalidate(f"tok_{current_account_data['auth_token'][:20]}")
                        except Exception:
                            pass
                    reconnects = 0
                    break
                await asyncio.sleep(min(reconnects * 0.5, 2.0))
            finally:
                if gateway_ping_task:
                    gateway_ping_task.cancel()
                if writer:
                    bot_state.unregister_writer(uid_str, writer)
                    squad_manager.unregister(uid_str)
                    await safe_close_writer(writer)
    except asyncio.CancelledError:
        print_info(f"[FUNCTIONAL] Task cancelled for UID: {uid_str}")
        raise
    finally:
        for m in play_matches:
            if not m.done():
                m.cancel()


async def informational(addrs, starter_packet, key, iv, region="ID", account_id="", max_reconnects=3):
    uid_str = str(account_id)
    reconnects = 0
    ip, port = addrs.split(":")
    while True:
        while uid_str and bot_state.is_paused(uid_str):
            await asyncio.sleep(1.0)
        writer = None
        ping_task = None
        try:
            resolved_ip = await resolve_host_cloudflare(ip)
            reader, writer = await asyncio.open_connection(resolved_ip, int(port))
            if uid_str:
                bot_state.register_writer(uid_str, writer)
            raw_sock = writer.get_extra_info('socket')
            if raw_sock:
                optimize_tcp_socket(raw_sock)
            writer.write(bytes.fromhex(starter_packet))
            await writer.drain()
            reconnects = 0
            try:
                init_ka = await send_keep_alive(region)
                if init_ka and writer and not writer.is_closing():
                    writer.write(init_ka)
                    await asyncio.wait_for(writer.drain(), timeout=3)
            except Exception:
                pass

            async def info_keepalive():
                ka_bytes = await send_keep_alive(region)
                while True:
                    await asyncio.sleep(5)
                    try:
                        if writer and not writer.is_closing():
                            writer.write(ka_bytes)
                            await writer.drain()
                    except Exception:
                        break

            ping_task = asyncio.create_task(info_keepalive())

            while True:
                if uid_str and bot_state.is_paused(uid_str):
                    if ping_task:
                        ping_task.cancel()
                    if uid_str:
                        bot_state.unregister_writer(uid_str, writer)
                    await safe_close_writer(writer)
                    writer = None
                    while bot_state.is_paused(uid_str):
                        await asyncio.sleep(1.0)
                    break
                try:
                    data = await asyncio.wait_for(reader.read(8192), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    raise ConnectionError("Connection closed")
        except asyncio.CancelledError:
            if ping_task:
                ping_task.cancel()
            if uid_str:
                bot_state.unregister_writer(uid_str, writer)
            await safe_close_writer(writer)
            raise
        except Exception:
            if ping_task:
                ping_task.cancel()
            if uid_str:
                bot_state.unregister_writer(uid_str, writer)
            await safe_close_writer(writer)
            reconnects += 1
            if reconnects > max_reconnects:
                await asyncio.sleep(3)
                reconnects = 0
            else:
                await asyncio.sleep(1)


# ==================== ACCOUNT PROCESSORS ====================
def _register_credentials(account_data: Dict):
    try:
        acc_id = str(account_data['account_id'])
        bot_state.account_credentials[acc_id] = account_data
        if account_data.get('auth_uid'):
            bot_state.account_credentials[str(account_data['auth_uid'])] = account_data
        if account_data.get('auth_token'):
            bot_state.account_credentials[f"tok_{account_data['auth_token'][:20]}"] = account_data
    except Exception:
        pass


async def refresh_account_profile(account_data_or_uid: Any):
    try:
        if isinstance(account_data_or_uid, str):
            uid = str(account_data_or_uid)
            account_data = bot_state.account_credentials.get(uid)
        else:
            account_data = account_data_or_uid
            uid = str(account_data.get('account_id'))
        if not account_data:
            return
        url = account_data.get('server_url')
        token = account_data.get('token')
        release_version = account_data.get('release_version')
        payload = account_data.get('login_payload_data')
        if not (url and token and release_version and payload):
            return
        res = await send_getlogin(payload, url, token, release_version)
        if res:
            res_proto, dict_res = res
            level = int(get_proto_field(dict_res, 6, 1))
            exp = int(get_proto_field(dict_res, 7, 0))
            likes = int(get_proto_field(dict_res, 8, 0))
            nickname = res_proto.nickname or get_proto_field(dict_res, 4, "")
            acc_id = str(account_data['account_id'])
            if exp > 0:
                old_exp = bot_state.accounts.get(acc_id, {}).get("current_exp", 0)
                old_level = int(bot_state.accounts.get(acc_id, {}).get("level", 0) or 0)
                bot_state.update_exp(acc_id, exp, level)
                if old_level > 0 and level > old_level:
                    print_success(f"🎉 [LEVEL UP] UID {acc_id} : Lvl {old_level} → {level}")
                    if level >= LONE_WOLF_UNLOCK_LEVEL and old_level < LONE_WOLF_UNLOCK_LEVEL:
                        print_success(f"🐺 [UNLOCK] UID {acc_id} → LONE WOLF unlocked (Lvl {level} ≥ {LONE_WOLF_UNLOCK_LEVEL})")
                elif old_exp and exp > old_exp:
                    diff = exp - old_exp
                    acc_state = bot_state.accounts.get(acc_id, {})
                    rem_e = acc_state.get('remaining_exp', 0)
                    nxt_l = acc_state.get('next_level', (level or 1) + 1)
                    pct_val = acc_state.get('progress_pct', 0)
                    print_success(f"[★] +{diff:,} EXP Gained | UID: {acc_id} | Lvl {acc_state.get('level', level)} ({pct_val}% - {rem_e:,} EXP to Lvl {nxt_l})")
            if likes > 0 and acc_id in bot_state.accounts:
                bot_state.accounts[acc_id]["likes"] = likes
            if nickname and acc_id in bot_state.accounts:
                bot_state.accounts[acc_id]["nickname"] = nickname
            _update_cache_profile(account_data, level, exp)
            account_data['level'] = level
            account_data['exp'] = exp
            if likes > 0:
                account_data['likes'] = likes
            if nickname:
                account_data['nickname'] = nickname
    except Exception:
        pass


async def process_account_uid_pass(uid: str, password: str) -> Optional[Dict]:
    cached = cache_get(uid)
    if cached:
        acc_id = str(cached['account_id'])
        nick = cached.get('nickname', f"Player_{acc_id}")
        lvl = cached.get('level', 1)
        exp_val = cached.get('exp', 0)
        mode_label = "LONE WOLF 🐺" if int(lvl) >= LONE_WOLF_UNLOCK_LEVEL else "BATTLE ROYALE"
        print_success(f"[✓] Online (Cache): UID {acc_id} | {nick} | Lvl {lvl} | Mode: {mode_label} | EXP: {exp_val:,}")
        bot_state.register_account(
            uid=acc_id, nickname=nick, region=cached.get('region', 'ID'),
            level=lvl, exp=exp_val, likes=cached.get('likes', 0)
        )
        _register_credentials(cached)
        return cached

    print_info(f"[LOGIN] Authenticating UID: {uid}...")
    try:
        async with _LOGIN_SEMAPHORE:
            verconfig_res = await version_config()
            if verconfig_res is None:
                return None
            release_version, client_version, server_url = verconfig_res
            tokengrant_response = await get_access_token(uid, password)
            if tokengrant_response is None:
                return None
            open_id, access_token, platform = tokengrant_response
            device_info = get_device_for_account(uid)
            login_payload_data = await build_majorlogin_payload(open_id, access_token, platform, client_version, device_info)
            majorlogin_response = await send_majorlogin(login_payload_data, release_version, server_url)
            if majorlogin_response is None:
                return None
            getlogin_result = await send_getlogin(login_payload_data, majorlogin_response.url, majorlogin_response.token, release_version)
            if getlogin_result is None:
                return None
            res_proto, dict_res = getlogin_result
        acc_id = str(majorlogin_response.account_id)
        level = int(get_proto_field(dict_res, 6, 1))
        exp = int(get_proto_field(dict_res, 7, 0))
        likes = int(get_proto_field(dict_res, 8, 0))
        nickname = res_proto.nickname or get_proto_field(dict_res, 4, f"Player_{acc_id}")
        region = majorlogin_response.region or get_proto_field(dict_res, 3, "ID")
        bot_state.register_account(uid=acc_id, nickname=nickname, region=region, level=level, exp=exp, likes=likes)
        mode_label = "LONE WOLF 🐺" if level >= LONE_WOLF_UNLOCK_LEVEL else "BATTLE ROYALE"
        print_success(f"[✓] Login Success: UID {acc_id} | {nickname} | Lvl {level} | Mode: {mode_label} | EXP: {exp:,}")
        account_data = {
            'account_id': majorlogin_response.account_id, 'nickname': nickname,
            'region': region, 'level': level, 'exp': exp, 'likes': likes,
            'open_id': open_id, 'access_token': access_token, 'platform': str(platform),
            'token': majorlogin_response.token, 'server_time': majorlogin_response.server_time,
            'aes_ak': majorlogin_response.aes_ak, 'iv_i': majorlogin_response.iv_i,
            'functional_addrs': res_proto.functional_addrs or get_proto_field(dict_res, 14),
            'informational_addrs': res_proto.informational_addrs or get_proto_field(dict_res, 32),
            'release_version': release_version, 'client_version': client_version,
            'server_url': majorlogin_response.url, 'login_payload_data': login_payload_data,
            'auth_type': 'guest', 'auth_uid': uid, 'auth_password': password
        }
        _register_credentials(account_data)
        cache_set(uid, account_data)
        return account_data
    except Exception as e:
        print_error(f"process_account_uid_pass error: {e}")
        return None


async def process_account_token(access_token: str) -> Optional[Dict]:
    cache_key = f"tok_{access_token[:20]}"
    cached = cache_get(cache_key)
    if cached:
        acc_id = str(cached['account_id'])
        nick = cached.get('nickname', f"Player_{acc_id}")
        lvl = cached.get('level', 1)
        exp_val = cached.get('exp', 0)
        mode_label = "LONE WOLF 🐺" if int(lvl) >= LONE_WOLF_UNLOCK_LEVEL else "BATTLE ROYALE"
        print_success(f"[✓] Online (Token Cache): UID {acc_id} | {nick} | Lvl {lvl} | Mode: {mode_label} | EXP: {exp_val:,}")
        bot_state.register_account(
            uid=acc_id, nickname=nick, region=cached.get('region', 'ID'),
            level=lvl, exp=exp_val, likes=cached.get('likes', 0)
        )
        _register_credentials(cached)
        return cached

    print_info("[LOGIN] Full login with Access Token...")
    try:
        async with _LOGIN_SEMAPHORE:
            verconfig_res = await version_config()
            if verconfig_res is None:
                return None
            release_version, client_version, server_url = verconfig_res
            url = f"https://100067.connect.garena.com/oauth/token/inspect?token={access_token}"
            hdrs = {
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "close",
                "Content-Type": "application/x-www-form-urlencoded",
                "Host": "100067.connect.garena.com",
                "User-Agent": "GarenaMSDK/4.0.19P4(G011A ;Android 9;en;US;)"
            }
            resp = await client.get(url, headers=hdrs, timeout=10.0)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if 'error' in data:
                return None
            open_id = data.get('open_id')
            platform = data.get('platform', 4)
            if not open_id:
                return None
            device_info = get_device_for_account(open_id)
            login_payload_data = await build_majorlogin_payload(open_id, access_token, str(platform), client_version, device_info)
            if not login_payload_data:
                return None
            majorlogin_response = await send_majorlogin(login_payload_data, release_version, server_url)
            if majorlogin_response is None:
                return None
            getlogin_result = await send_getlogin(
                login_payload_data, majorlogin_response.url,
                majorlogin_response.token, release_version
            )
            if getlogin_result is None:
                return None
            res_proto, dict_res = getlogin_result
        acc_id = str(majorlogin_response.account_id)
        level = int(get_proto_field(dict_res, 6, 1))
        exp = int(get_proto_field(dict_res, 7, 0))
        likes = int(get_proto_field(dict_res, 8, 0))
        nickname = res_proto.nickname or get_proto_field(dict_res, 4, f"Player_{acc_id}")
        region = majorlogin_response.region or get_proto_field(dict_res, 3, "ID")
        bot_state.register_account(uid=acc_id, nickname=nickname, region=region, level=level, exp=exp, likes=likes)
        mode_label = "LONE WOLF 🐺" if level >= LONE_WOLF_UNLOCK_LEVEL else "BATTLE ROYALE"
        print_success(f"[✓] Login Success (Token): UID {acc_id} | {nickname} | Lvl {level} | Mode: {mode_label} | EXP: {exp:,}")
        account_data = {
            'account_id': majorlogin_response.account_id, 'nickname': nickname,
            'region': region, 'level': level, 'exp': exp, 'likes': likes,
            'open_id': open_id, 'access_token': access_token, 'platform': str(platform),
            'token': majorlogin_response.token, 'server_time': majorlogin_response.server_time,
            'aes_ak': majorlogin_response.aes_ak, 'iv_i': majorlogin_response.iv_i,
            'functional_addrs': res_proto.functional_addrs or get_proto_field(dict_res, 14),
            'informational_addrs': res_proto.informational_addrs or get_proto_field(dict_res, 32),
            'release_version': release_version, 'client_version': client_version,
            'server_url': majorlogin_response.url, 'login_payload_data': login_payload_data,
            'auth_type': 'token', 'auth_token': access_token
        }
        _register_credentials(account_data)
        cache_set(cache_key, account_data)
        return account_data
    except Exception as e:
        print_error(f"process_account_token error: {e}")
        return None


async def run_account_worker(account_data: Dict, label: str):
    acc_id = str(account_data['account_id'])
    informational_task = None
    exp_task = None
    try:
        reg = account_data.get('region', 'ID')
        tcp_packet_online = await build_tcp_startup_packet(
            account_data['account_id'], account_data['token'], account_data['server_time'],
            account_data['aes_ak'], account_data['iv_i'], region=reg, typ='OnLine'
        )
        tcp_packet_chat = await build_tcp_startup_packet(
            account_data['account_id'], account_data['token'], account_data['server_time'],
            account_data['aes_ak'], account_data['iv_i'], region=reg, typ='ChaT'
        )
        informational_task = asyncio.create_task(
            informational(account_data['informational_addrs'], tcp_packet_chat,
                          account_data['aes_ak'], account_data['iv_i'],
                          region=reg, account_id=acc_id)
        )

        async def exp_refresher():
            try:
                fresh = bot_state.account_credentials.get(acc_id)
                if fresh:
                    await refresh_account_profile(fresh)
            except Exception as e:
                print_warning(f"[EXP-REFRESH] initial refresh error ({acc_id}): {e}")
            while True:
                await asyncio.sleep(EXP_REFRESH_INTERVAL + random.uniform(-3.0, 3.0))
                try:
                    fresh = bot_state.account_credentials.get(acc_id)
                    if fresh:
                        await refresh_account_profile(fresh)
                except Exception as e:
                    print_warning(f"[EXP-REFRESH] periodic refresh error ({acc_id}): {e}")

        exp_task = asyncio.create_task(exp_refresher())
        functional_task = asyncio.create_task(
            functional_lone_wolf(
                account_data['functional_addrs'], tcp_packet_online,
                account_data['region'], account_data['client_version'],
                account_data['aes_ak'], account_data['iv_i'],
                account_id=acc_id, account_data=account_data,
                account_level=int(account_data.get('level', 1) or 1)
            )
        )
        await functional_task
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print_error(f"run_account_worker error for {label}: {e}")
    finally:
        for t in (informational_task, exp_task):
            if t and not t.done():
                t.cancel()
        for t in (informational_task, exp_task):
            if t:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass


async def account_loop_guest(uid: str, password: str):
    uid_str = str(uid)
    bot_state.account_workers[uid_str] = asyncio.current_task()
    acc_id = None
    while True:
        try:
            print_info(f"[LOGIN] Starting login for Guest UID: {uid_str}...")
            try:
                bot_state.update_status(uid_str, "CONNECTING")
            except Exception:
                pass
            account_data = await process_account_uid_pass(uid_str, password)
            if not account_data:
                print_error(f"Login failed for UID: {uid_str}. Retrying in 15 seconds...")
                try:
                    bot_state.update_status(uid_str, "ERROR")
                except Exception:
                    pass
                await asyncio.sleep(15)
                continue
            acc_id = str(account_data['account_id'])
            bot_state.account_workers[acc_id] = asyncio.current_task()
            bot_state.account_workers[uid_str] = asyncio.current_task()
            await run_account_worker(account_data, uid_str)
            print_warning(f"Session finished for {uid_str}. Reconnecting in 3s...")
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            print_warning(f"Worker for {uid_str} stopped.")
            try:
                bot_state.update_status(uid_str, "OFFLINE")
                if acc_id:
                    bot_state.update_status(acc_id, "OFFLINE")
            except Exception:
                pass
            break
        except Exception as e:
            print_error(f"Error for UID {uid_str}: {e}. Retrying in 10s...")
            await asyncio.sleep(10)


async def account_loop_token(token: str):
    tok_key = token[:16]
    while True:
        try:
            account_data = await process_account_token(token)
            if not account_data:
                await asyncio.sleep(15)
                continue
            acc_id = str(account_data['account_id'])
            bot_state.account_workers[acc_id] = asyncio.current_task()
            bot_state.account_workers[tok_key] = asyncio.current_task()
            await run_account_worker(account_data, acc_id)
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(8)


# ==================== ACCOUNTS LOADER ====================
def load_accounts():
    accounts = []
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    accounts = data
        except Exception as e:
            print_error(f"Could not load {ACCOUNTS_FILE}: {e}")
    if not accounts and FALLBACK_UID and FALLBACK_PASSWORD:
        accounts.append({"uid": FALLBACK_UID, "password": FALLBACK_PASSWORD})
    return accounts


# ==================== MAIN ====================
async def main():
    global _squad_loop_task

    print_colored("╔════════════════════════════════════════════════════════════╗", Colors.CYAN)
    print_colored("║   ⚡ AUTO LEVEL UP BOT — BR + SQUAD → LONE WOLF SOLO 🐺    ║", Colors.CYAN)
    print_colored("║     Anti-AFK ON  •  EXP Refresh 25s  •  Post-Match Sync    ║", Colors.WHITE)
    print_colored(f"║         Web Dashboard: http://localhost:{WEB_PORT}              ║", Colors.GREEN)
    print_colored("╚════════════════════════════════════════════════════════════╝", Colors.CYAN)

    print_colored("-" * 60, Colors.MAGENTA)
    print_info(f"🔀 MODE SWITCH:")
    print_info(f"   • Level < {LONE_WOLF_UNLOCK_LEVEL}  → BATTLE ROYALE + SQUAD  (mode_id={BR_MODE_ID}, map_id={BR_MAP_ID})")
    print_info(f"   • Level ≥ {LONE_WOLF_UNLOCK_LEVEL}  → LONE WOLF SOLO 🐺     (mode_id={LONE_WOLF_MODE_ID}, map_id={LONE_WOLF_MAP_ID})")
    print_info(f"🤝 SQUAD: {'ON (BR only)' if SQUAD_MODE_ENABLED else 'OFF'}")
    print_info(f"🎮 ANTI-AFK: ON (fire chance {ANTI_AFK_FIRE_CHANCE}, interval {ANTI_AFK_MIN_INTERVAL}-{ANTI_AFK_MAX_INTERVAL}s)")
    print_info(f"📈 EXP REFRESH: {EXP_REFRESH_INTERVAL}s + post-match {EXP_REFRESH_AFTER_MATCH_DELAY}s")
    print_colored("-" * 60, Colors.MAGENTA)

    try:
        await start_web_dashboard(host=WEB_HOST, port=WEB_PORT)
        print_success(f"[✓] Dashboard UI Active: http://localhost:{WEB_PORT}")
    except Exception as e:
        print_error(f"Could not start web dashboard: {e}")

    async def on_account_added_handler(data):
        sync_devices_with_accounts()
        if "token" in data and data["token"]:
            t = str(data["token"]).strip()
            task = asyncio.create_task(account_loop_token(t))
            bot_state.account_workers[t[:16]] = task
        elif "uid" in data and "password" in data:
            u = str(data["uid"]).strip()
            p = str(data["password"]).strip()
            task = asyncio.create_task(account_loop_guest(u, p))
            bot_state.account_workers[u] = task

    async def on_refresh_account_handler(uid):
        await refresh_account_profile(uid)

    bot_state.refresh_callbacks["on_account_added"] = on_account_added_handler
    bot_state.refresh_callbacks["on_refresh_account"] = on_refresh_account_handler

    sync_devices_with_accounts()
    accounts = load_accounts()

    if not accounts:
        print_warning(f"[!] No accounts found in {ACCOUNTS_FILE}. Add accounts via Web Dashboard: http://localhost:{WEB_PORT}")
    else:
        print_success(f"[✓] Loaded {len(accounts)} accounts from {ACCOUNTS_FILE}")

    for idx, acc in enumerate(accounts):
        if "token" in acc and acc["token"]:
            tok = str(acc["token"]).strip()
            t = asyncio.create_task(account_loop_token(tok))
            bot_state.account_workers[tok[:16]] = t
        elif "uid" in acc and "password" in acc and acc["uid"]:
            u = str(acc["uid"])
            t = asyncio.create_task(account_loop_guest(u, acc["password"]))
            bot_state.account_workers[u] = t
        if idx < len(accounts) - 1:
            await asyncio.sleep(0.35)

    if SQUAD_MODE_ENABLED:
        _squad_loop_task = asyncio.create_task(squad_orchestrator_loop())
        print_success(f"[✓] Squad Orchestrator ACTIVE — BR-ONLY (max {SQUAD_MAX_MEMBERS} members)")

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print_warning("\n[STOP] Shutting down all accounts...")
        if _squad_loop_task:
            _squad_loop_task.cancel()
        for t in list(bot_state.account_workers.values()):
            t.cancel()
        await asyncio.gather(*bot_state.account_workers.values(), return_exceptions=True)
        print_success("All sessions cleanly closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_warning("\nProgram stopped by user.")