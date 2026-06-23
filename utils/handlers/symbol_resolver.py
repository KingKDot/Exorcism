import ctypes
import os
import struct
import urllib.request
import uuid
from ctypes import wintypes
from typing import Iterable, Optional, Tuple


MAX_SYM_NAME = 2000
SYMOPT_UNDNAME = 0x00000002
SYMOPT_DEFERRED_LOADS = 0x00000004
SYMOPT_LOAD_LINES = 0x00000010
SYMOPT_FAIL_CRITICAL_ERRORS = 0x00000200
SYMBOL_SERVER = "https://msdl.microsoft.com/download/symbols"


class SYMBOL_INFO(ctypes.Structure):
    _fields_ = [
        ("SizeOfStruct", wintypes.ULONG),
        ("TypeIndex", wintypes.ULONG),
        ("Reserved", ctypes.c_ulonglong * 2),
        ("Index", wintypes.ULONG),
        ("Size", wintypes.ULONG),
        ("ModBase", ctypes.c_ulonglong),
        ("Flags", wintypes.ULONG),
        ("Value", ctypes.c_ulonglong),
        ("Address", ctypes.c_ulonglong),
        ("Register", wintypes.ULONG),
        ("Scope", wintypes.ULONG),
        ("Tag", wintypes.ULONG),
        ("NameLen", wintypes.ULONG),
        ("MaxNameLen", wintypes.ULONG),
        ("Name", ctypes.c_char * MAX_SYM_NAME),
    ]


def _read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _read_u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _rva_to_file_offset(rva: int, sections: list[Tuple[int, int, int, int]]) -> Optional[int]:
    for virtual_address, virtual_size, raw_size, raw_pointer in sections:
        span = max(virtual_size, raw_size)
        if virtual_address <= rva < virtual_address + span:
            return raw_pointer + (rva - virtual_address)
    return None


def _parse_cmd_debug_info(cmd_path: str) -> Optional[Tuple[str, str, int]]:
    with open(cmd_path, "rb") as file:
        data = file.read()

    pe_offset = _read_u32(data, 0x3C)
    if data[pe_offset : pe_offset + 4] != b"PE\0\0":
        return None

    coff_offset = pe_offset + 4
    section_count = _read_u16(data, coff_offset + 2)
    optional_header_size = _read_u16(data, coff_offset + 16)
    optional_offset = coff_offset + 20
    magic = _read_u16(data, optional_offset)

    if magic == 0x20B:
        image_base = _read_u64(data, optional_offset + 24)
        data_directory_offset = optional_offset + 112
    elif magic == 0x10B:
        image_base = _read_u32(data, optional_offset + 28)
        data_directory_offset = optional_offset + 96
    else:
        return None

    debug_directory_rva = _read_u32(data, data_directory_offset + 6 * 8)
    debug_directory_size = _read_u32(data, data_directory_offset + 6 * 8 + 4)
    if not debug_directory_rva or debug_directory_size < 28:
        return None

    section_offset = optional_offset + optional_header_size
    sections = []
    for index in range(section_count):
        offset = section_offset + index * 40
        virtual_size = _read_u32(data, offset + 8)
        virtual_address = _read_u32(data, offset + 12)
        raw_size = _read_u32(data, offset + 16)
        raw_pointer = _read_u32(data, offset + 20)
        sections.append((virtual_address, virtual_size, raw_size, raw_pointer))

    debug_offset = _rva_to_file_offset(debug_directory_rva, sections)
    if debug_offset is None:
        return None

    for offset in range(debug_offset, debug_offset + debug_directory_size, 28):
        debug_type = _read_u32(data, offset + 12)
        size_of_data = _read_u32(data, offset + 16)
        pointer_to_raw_data = _read_u32(data, offset + 24)
        if debug_type != 2 or size_of_data < 24:
            continue

        codeview = data[pointer_to_raw_data : pointer_to_raw_data + size_of_data]
        if codeview[:4] != b"RSDS":
            continue

        signature = uuid.UUID(bytes_le=codeview[4:20]).hex.upper()
        age = _read_u32(codeview, 20)
        pdb_name = codeview[24:].split(b"\0", 1)[0].decode("utf-8", errors="ignore")
        return f"{signature}{age}", os.path.basename(pdb_name), image_base

    return None


def _ensure_pdb(cmd_path: str, symbol_cache: str) -> Optional[int]:
    debug_info = _parse_cmd_debug_info(cmd_path)
    if debug_info is None:
        return None

    signature_age, pdb_name, image_base = debug_info
    pdb_path = os.path.join(symbol_cache, pdb_name, signature_age, pdb_name)
    if not os.path.exists(pdb_path):
        os.makedirs(os.path.dirname(pdb_path), exist_ok=True)
        url = f"{SYMBOL_SERVER}/{pdb_name}/{signature_age}/{pdb_name}"
        urllib.request.urlretrieve(url, pdb_path)

    return image_base


def resolve_cmd_symbol_rvas(
    symbol_names: Iterable[str], cmd_path: Optional[str] = None
) -> dict[str, int]:
    if cmd_path is None:
        cmd_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    symbol_cache = os.path.join(repo_root, ".symbols")

    try:
        image_base = _ensure_pdb(cmd_path, symbol_cache)
    except Exception:
        image_base = None

    if image_base is None:
        image_base = 0x140000000

    dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process = kernel32.GetCurrentProcess()

    dbghelp.SymSetOptions(
        SYMOPT_UNDNAME
        | SYMOPT_DEFERRED_LOADS
        | SYMOPT_LOAD_LINES
        | SYMOPT_FAIL_CRITICAL_ERRORS
    )

    dbghelp.SymInitializeW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.BOOL]
    dbghelp.SymInitializeW.restype = wintypes.BOOL
    dbghelp.SymLoadModuleExW.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_ulonglong,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    dbghelp.SymLoadModuleExW.restype = ctypes.c_ulonglong
    dbghelp.SymFromName.argtypes = [
        wintypes.HANDLE,
        ctypes.c_char_p,
        ctypes.POINTER(SYMBOL_INFO),
    ]
    dbghelp.SymFromName.restype = wintypes.BOOL
    dbghelp.SymCleanup.argtypes = [wintypes.HANDLE]
    dbghelp.SymCleanup.restype = wintypes.BOOL

    symbol_paths = [
        symbol_cache,
        r"C:\symbols",
        os.environ.get("_NT_SYMBOL_PATH", ""),
    ]
    search_path = ";".join(path for path in symbol_paths if path)

    if not dbghelp.SymInitializeW(process, search_path, False):
        return {}

    try:
        module_base = dbghelp.SymLoadModuleExW(
            process,
            None,
            cmd_path,
            None,
            image_base,
            0,
            None,
            0,
        )
        if not module_base:
            return {}

        resolved = {}
        for symbol_name in symbol_names:
            symbol = SYMBOL_INFO()
            symbol.SizeOfStruct = ctypes.sizeof(SYMBOL_INFO) - MAX_SYM_NAME
            symbol.MaxNameLen = MAX_SYM_NAME
            if dbghelp.SymFromName(
                process, symbol_name.encode("ascii"), ctypes.byref(symbol)
            ):
                resolved[symbol_name] = int(symbol.Address - symbol.ModBase)

        return resolved
    finally:
        dbghelp.SymCleanup(process)


def resolve_cmd_symbol_rva(
    symbol_name: str, cmd_path: Optional[str] = None
) -> Optional[int]:
    return resolve_cmd_symbol_rvas([symbol_name], cmd_path).get(symbol_name)


def resolve_find_fix_and_run_rva(cmd_path: Optional[str] = None) -> Optional[int]:
    return resolve_cmd_symbol_rva("FindFixAndRun", cmd_path)
