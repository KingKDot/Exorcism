#![allow(non_snake_case)]

mod cmd;
mod debugger;
mod hooks;
mod logging;
mod win;

use std::ffi::c_void;
use win::{Bool, Dword, Hmodule, DLL_PROCESS_ATTACH, DLL_PROCESS_DETACH, TRUE};

#[no_mangle]
pub unsafe extern "system" fn DllMain(
    _module: Hmodule,
    reason: Dword,
    _reserved: *mut c_void,
) -> Bool {
    match reason {
        DLL_PROCESS_ATTACH => hooks::attach(),
        DLL_PROCESS_DETACH => hooks::detach(),
        _ => {}
    }

    TRUE
}
