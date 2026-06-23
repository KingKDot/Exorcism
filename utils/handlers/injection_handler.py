import ctypes
import ctypes.wintypes
import os
import subprocess
import time
from typing import Mapping, Optional, Tuple


PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
PAGE_READWRITE = 0x04
THREAD_ALL_ACCESS = 0x1F03FF

kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
]
OpenProcess.restype = ctypes.wintypes.HANDLE

VirtualAllocEx = kernel32.VirtualAllocEx
VirtualAllocEx.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.DWORD,
]
VirtualAllocEx.restype = ctypes.wintypes.LPVOID

WriteProcessMemory = kernel32.WriteProcessMemory
WriteProcessMemory.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.LPVOID,
    ctypes.wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
WriteProcessMemory.restype = ctypes.wintypes.BOOL

GetModuleHandle = kernel32.GetModuleHandleW
GetModuleHandle.argtypes = [ctypes.wintypes.LPCWSTR]
GetModuleHandle.restype = ctypes.wintypes.HMODULE

GetProcAddress = kernel32.GetProcAddress
GetProcAddress.argtypes = [ctypes.wintypes.HMODULE, ctypes.wintypes.LPCSTR]
GetProcAddress.restype = ctypes.wintypes.LPVOID

CreateRemoteThread = kernel32.CreateRemoteThread
CreateRemoteThread.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.wintypes.LPVOID,
    ctypes.wintypes.LPVOID,
    ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
CreateRemoteThread.restype = ctypes.wintypes.HANDLE

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
CloseHandle.restype = ctypes.wintypes.BOOL


def get_process_id_by_name(process_name: str) -> Optional[int]:
    max_processes = 1024
    process_ids = (ctypes.wintypes.DWORD * max_processes)()
    bytes_returned = ctypes.wintypes.DWORD()

    if not psapi.EnumProcesses(
        ctypes.byref(process_ids),
        ctypes.sizeof(process_ids),
        ctypes.byref(bytes_returned),
    ):
        return None

    num_processes = bytes_returned.value // ctypes.sizeof(ctypes.wintypes.DWORD)

    for i in range(num_processes):
        pid = process_ids[i]
        if pid == 0:
            continue

        # Open process to get its name
        process_handle = OpenProcess(
            0x0400 | 0x0010, False, pid
        )  # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if not process_handle:
            continue

        try:
            # Get process name
            process_name_buffer = (ctypes.c_char * 260)()
            if psapi.GetProcessImageFileNameA(process_handle, process_name_buffer, 260):
                full_path = process_name_buffer.value.decode("utf-8", errors="ignore")
                current_process_name = os.path.basename(full_path)

                if current_process_name.lower() == process_name.lower():
                    return pid
        finally:
            CloseHandle(process_handle)

    return None


def launch_cmd_and_get_pid(
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[Optional[subprocess.Popen], Optional[int]]:
    """
    Launch a new cmd.exe process and return the process object and PID.

    Returns:
        Tuple of (process_object, pid) if successful, (None, None) if failed
    """
    try:
        process = subprocess.Popen(
            ["cmd.exe"], creationflags=subprocess.CREATE_NEW_CONSOLE, env=env
        )

        time.sleep(2)

        pid = process.pid
        return process, pid

    except Exception as e:
        print(f"Error launching cmd.exe: {e}")
        return None, None


def inject_dll(
    dll_path: str, process_name: Optional[str] = None, process_id: Optional[int] = None
) -> bool:
    if not os.path.exists(dll_path):
        print(f"Error: DLL file not found: {dll_path}")
        return False

    if not dll_path.lower().endswith(".dll"):
        print(f"Error: File is not a DLL: {dll_path}")
        return False

    if process_id is not None:
        pid = process_id
        print(f"Using provided PID: {pid}")
    elif process_name is not None:
        pid = get_process_id_by_name(process_name)
        if pid is None:
            print(f"Error: Process '{process_name}' not found")
            return False
        print(f"Found process '{process_name}' with PID: {pid}")
    else:
        print("Error: Either process_name or process_id must be provided")
        return False

    process_handle = OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not process_handle:
        print(f"Error: Failed to open process (PID: {pid}). Check permissions.")
        return False

    try:
        kernel32_handle = GetModuleHandle("kernel32.dll")
        if not kernel32_handle:
            print("Error: Failed to get kernel32.dll handle")
            return False

        load_library_addr = GetProcAddress(kernel32_handle, b"LoadLibraryA")
        if not load_library_addr:
            print("Error: Failed to get LoadLibraryA address")
            return False

        dll_path_bytes = dll_path.encode("utf-8") + b"\0"
        dll_path_size = len(dll_path_bytes)

        remote_memory = VirtualAllocEx(
            process_handle,
            None,
            dll_path_size,
            MEM_COMMIT | MEM_RESERVE,
            PAGE_READWRITE,
        )
        if not remote_memory:
            print("Error: Failed to allocate memory in target process")
            return False

        bytes_written = ctypes.c_size_t()
        if not WriteProcessMemory(
            process_handle,
            remote_memory,
            dll_path_bytes,
            dll_path_size,
            ctypes.byref(bytes_written),
        ):
            print("Error: Failed to write DLL path to target process memory")
            return False

        thread_id = ctypes.wintypes.DWORD()
        thread_handle = CreateRemoteThread(
            process_handle,
            None,
            0,
            load_library_addr,
            remote_memory,
            0,
            ctypes.byref(thread_id),
        )

        if not thread_handle:
            print("Error: Failed to create remote thread")
            return False

        print(f"Successfully injected '{dll_path}' into process (PID: {pid})")
        CloseHandle(thread_handle)
        return True

    except Exception as e:
        print(f"Error during injection: {e}")
        return False
    finally:
        CloseHandle(process_handle)
