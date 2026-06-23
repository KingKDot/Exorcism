use std::ffi::c_void;
use std::mem::size_of;
use std::slice;

pub type Bool = i32;
pub type Dword = u32;
pub type Handle = *mut c_void;
pub type Hmodule = *mut c_void;

pub const TRUE: Bool = 1;
pub const DLL_PROCESS_ATTACH: Dword = 1;
pub const DLL_PROCESS_DETACH: Dword = 0;

const MEM_COMMIT: Dword = 0x1000;
const PAGE_READONLY: Dword = 0x02;
const PAGE_READWRITE: Dword = 0x04;
const PAGE_EXECUTE_READ: Dword = 0x20;
const PAGE_EXECUTE_READWRITE: Dword = 0x40;

#[repr(C)]
struct MemoryBasicInformation {
    BaseAddress: *mut c_void,
    AllocationBase: *mut c_void,
    AllocationProtect: Dword,
    PartitionId: u16,
    RegionSize: usize,
    State: Dword,
    Protect: Dword,
    Type: Dword,
}

#[link(name = "kernel32")]
extern "system" {
    pub fn GetModuleHandleW(lpModuleName: *const u16) -> Hmodule;
    fn VirtualQuery(
        lpAddress: *const c_void,
        lpBuffer: *mut MemoryBasicInformation,
        dwLength: usize,
    ) -> usize;
}

pub fn safe_extract_wide_string(ptr: *const u16, max_len: usize) -> String {
    let len = unsafe { safe_wcs_len(ptr, max_len) };
    if len == 0 {
        return String::new();
    }

    let wide = unsafe { slice::from_raw_parts(ptr, len) };
    String::from_utf16_lossy(wide)
}

unsafe fn safe_wcs_len(ptr: *const u16, max_len: usize) -> usize {
    if !is_safe_pointer(ptr.cast()) {
        return 0;
    }

    let mut len = 0;
    while len < max_len && *ptr.add(len) != 0 {
        len += 1;
    }
    len
}

pub fn is_safe_pointer(ptr: *const c_void) -> bool {
    let addr = ptr as usize;
    if ptr.is_null() || addr < 0x10000 || addr > 0x7FFF_FFFF_FFFF {
        return false;
    }

    let mut mbi = MemoryBasicInformation {
        BaseAddress: std::ptr::null_mut(),
        AllocationBase: std::ptr::null_mut(),
        AllocationProtect: 0,
        PartitionId: 0,
        RegionSize: 0,
        State: 0,
        Protect: 0,
        Type: 0,
    };

    let queried = unsafe { VirtualQuery(ptr, &mut mbi, size_of::<MemoryBasicInformation>()) };

    queried != 0
        && mbi.State == MEM_COMMIT
        && (mbi.Protect
            & (PAGE_READONLY | PAGE_READWRITE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE))
            != 0
}
