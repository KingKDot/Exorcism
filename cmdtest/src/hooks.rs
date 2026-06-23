use crate::cmd::{CmdNode, ANDTYP, CMDTYP, CSTYP, ORTYP, PIPTYP, RIO_PIPE};
use crate::debugger::{self, BreakOutcome};
use crate::logging::log_json_entry;
use crate::win::{is_safe_pointer, GetModuleHandleW, Hmodule};
use detour::GenericDetour;
use std::cell::Cell;
use std::ptr::null;
use std::sync::OnceLock;

const DEFAULT_FIND_FIX_AND_RUN_RVA: usize = 0x116B0;
const DEFAULT_DISPATCH_RVA: usize = 0xEA40;
const DEFAULT_EPIPE_RVA: usize = 0xF320;
const DEFAULT_ECOMSEP_RVA: usize = 0x201E0;
const DEFAULT_EAND_RVA: usize = 0x22450;
const DEFAULT_EOR_RVA: usize = 0x22410;

type FindFixAndRun = unsafe extern "C" fn(*mut CmdNode) -> i32;
type Dispatch = unsafe extern "C" fn(i32, *mut CmdNode) -> i32;
type OperatorHandler = unsafe extern "C" fn(*mut CmdNode) -> i32;

static FIND_FIX_AND_RUN_HOOK: OnceLock<GenericDetour<FindFixAndRun>> = OnceLock::new();
static DISPATCH_HOOK: OnceLock<GenericDetour<Dispatch>> = OnceLock::new();
static EPIPE_HOOK: OnceLock<GenericDetour<OperatorHandler>> = OnceLock::new();
static ECOMSEP_HOOK: OnceLock<GenericDetour<OperatorHandler>> = OnceLock::new();
static EAND_HOOK: OnceLock<GenericDetour<OperatorHandler>> = OnceLock::new();
static EOR_HOOK: OnceLock<GenericDetour<OperatorHandler>> = OnceLock::new();

thread_local! {
    static SUPPRESS_PIPE_CHILDREN: Cell<u32> = const { Cell::new(0) };
}

pub unsafe fn attach() {
    let cmd_module = GetModuleHandleW(null());
    if cmd_module.is_null() {
        log_json_entry(
            "hook_error",
            None,
            None,
            None,
            Some("Failed to locate cmd.exe module"),
        );
        return;
    }

    attach_find_fix_and_run_hook(cmd_module);
    attach_dispatch_hook(cmd_module);
    attach_operator_hook(
        cmd_module,
        "ePipe",
        "EXORCISM_EPIPE_RVA",
        DEFAULT_EPIPE_RVA,
        hooked_epipe,
        &EPIPE_HOOK,
    );
    attach_operator_hook(
        cmd_module,
        "eComSep",
        "EXORCISM_ECOMSEP_RVA",
        DEFAULT_ECOMSEP_RVA,
        hooked_ecomsep,
        &ECOMSEP_HOOK,
    );
    attach_operator_hook(
        cmd_module,
        "eAnd",
        "EXORCISM_EAND_RVA",
        DEFAULT_EAND_RVA,
        hooked_eand,
        &EAND_HOOK,
    );
    attach_operator_hook(
        cmd_module,
        "eOr",
        "EXORCISM_EOR_RVA",
        DEFAULT_EOR_RVA,
        hooked_eor,
        &EOR_HOOK,
    );
}

unsafe fn attach_find_fix_and_run_hook(cmd_module: Hmodule) {
    let rva = configured_rva(
        "EXORCISM_FIND_FIX_AND_RUN_RVA",
        DEFAULT_FIND_FIX_AND_RUN_RVA,
    );
    let target_address = cmd_module as usize + rva;
    log_json_entry(
        "hook_target",
        None,
        None,
        None,
        Some(&format!(
            "cmd.exe base=0x{:X}, FindFixAndRun RVA=0x{:X}, target=0x{:X}",
            cmd_module as usize, rva, target_address
        )),
    );

    let original: FindFixAndRun = std::mem::transmute(target_address);
    match GenericDetour::new(
        original,
        hooked_find_fix_and_run as extern "C" fn(*mut CmdNode) -> i32,
    )
    .and_then(|hook| {
        hook.enable()?;
        Ok(hook)
    }) {
        Ok(hook) => match FIND_FIX_AND_RUN_HOOK.set(hook) {
            Ok(()) => {
                log_json_entry(
                    "hook_status",
                    None,
                    None,
                    None,
                    Some("FindFixAndRun hook initialized successfully"),
                );
            }
            Err(hook) => {
                let _ = hook.disable();
                log_json_entry(
                    "hook_error",
                    None,
                    None,
                    None,
                    Some("FindFixAndRun hook was already initialized"),
                );
            }
        },
        Err(error) => {
            log_json_entry(
                "hook_error",
                None,
                None,
                None,
                Some(&format!("Failed to initialize FindFixAndRun hook: {error}")),
            );
        }
    };
}

unsafe fn attach_dispatch_hook(cmd_module: Hmodule) {
    let rva = configured_rva("EXORCISM_DISPATCH_RVA", DEFAULT_DISPATCH_RVA);
    let target_address = cmd_module as usize + rva;
    log_json_entry(
        "hook_target",
        None,
        None,
        None,
        Some(&format!(
            "cmd.exe base=0x{:X}, Dispatch RVA=0x{:X}, target=0x{:X}",
            cmd_module as usize, rva, target_address
        )),
    );

    let original: Dispatch = std::mem::transmute(target_address);
    match GenericDetour::new(
        original,
        hooked_dispatch as extern "C" fn(i32, *mut CmdNode) -> i32,
    )
    .and_then(|hook| {
        hook.enable()?;
        Ok(hook)
    }) {
        Ok(hook) => match DISPATCH_HOOK.set(hook) {
            Ok(()) => {
                log_json_entry(
                    "hook_status",
                    None,
                    None,
                    None,
                    Some("Dispatch hook initialized successfully"),
                );
            }
            Err(hook) => {
                let _ = hook.disable();
                log_json_entry(
                    "hook_error",
                    None,
                    None,
                    None,
                    Some("Dispatch hook was already initialized"),
                );
            }
        },
        Err(error) => {
            log_json_entry(
                "hook_error",
                None,
                None,
                None,
                Some(&format!("Failed to initialize Dispatch hook: {error}")),
            );
        }
    };
}

unsafe fn attach_operator_hook(
    cmd_module: Hmodule,
    name: &str,
    env_name: &str,
    fallback_rva: usize,
    detour: OperatorHandler,
    storage: &'static OnceLock<GenericDetour<OperatorHandler>>,
) {
    let rva = configured_rva(env_name, fallback_rva);
    let target_address = cmd_module as usize + rva;
    log_json_entry(
        "hook_target",
        None,
        None,
        None,
        Some(&format!(
            "cmd.exe base=0x{:X}, {} RVA=0x{:X}, target=0x{:X}",
            cmd_module as usize, name, rva, target_address
        )),
    );

    let original: OperatorHandler = std::mem::transmute(target_address);
    match GenericDetour::new(original, detour).and_then(|hook| {
        hook.enable()?;
        Ok(hook)
    }) {
        Ok(hook) => match storage.set(hook) {
            Ok(()) => {
                log_json_entry(
                    "hook_status",
                    None,
                    None,
                    None,
                    Some(&format!("{name} hook initialized successfully")),
                );
            }
            Err(hook) => {
                let _ = hook.disable();
                log_json_entry(
                    "hook_error",
                    None,
                    None,
                    None,
                    Some(&format!("{name} hook was already initialized")),
                );
            }
        },
        Err(error) => {
            log_json_entry(
                "hook_error",
                None,
                None,
                None,
                Some(&format!("Failed to initialize {name} hook: {error}")),
            );
        }
    };
}

pub unsafe fn detach() {
    log_json_entry(
        "hook_status",
        None,
        None,
        None,
        Some("FindFixAndRun hook being removed"),
    );

    if let Some(hook) = FIND_FIX_AND_RUN_HOOK.get() {
        let _ = hook.disable();
    }

    if let Some(hook) = DISPATCH_HOOK.get() {
        let _ = hook.disable();
    }

    if let Some(hook) = EPIPE_HOOK.get() {
        let _ = hook.disable();
    }

    if let Some(hook) = ECOMSEP_HOOK.get() {
        let _ = hook.disable();
    }

    if let Some(hook) = EAND_HOOK.get() {
        let _ = hook.disable();
    }

    if let Some(hook) = EOR_HOOK.get() {
        let _ = hook.disable();
    }
}

extern "C" fn hooked_find_fix_and_run(cmdnode: *mut CmdNode) -> i32 {
    if pipe_children_suppressed() {
        return call_find_fix_and_run(cmdnode);
    }

    match debugger::handle_command_breakpoint(cmdnode, "FindFixAndRun", None) {
        BreakOutcome::Continue => call_find_fix_and_run(cmdnode),
        BreakOutcome::Skip(result) => result,
    }
}

extern "C" fn hooked_dispatch(rio_type: i32, cmdnode: *mut CmdNode) -> i32 {
    if is_safe_pointer(cmdnode.cast()) {
        let node_type = unsafe { (*cmdnode).command_type };
        if rio_type == RIO_PIPE && node_type == CMDTYP && !pipe_children_suppressed() {
            return match debugger::handle_command_breakpoint(
                cmdnode,
                "DispatchPipe",
                Some(rio_type),
            ) {
                BreakOutcome::Continue => call_dispatch(rio_type, cmdnode),
                BreakOutcome::Skip(result) => result,
            };
        }
    }

    call_dispatch(rio_type, cmdnode)
}

extern "C" fn hooked_epipe(cmdnode: *mut CmdNode) -> i32 {
    handle_operator_group(
        cmdnode,
        "|",
        PIPTYP,
        "DispatchPipeGroup",
        RIO_PIPE,
        &EPIPE_HOOK,
    )
}

extern "C" fn hooked_ecomsep(cmdnode: *mut CmdNode) -> i32 {
    handle_operator_group(
        cmdnode,
        "&",
        CSTYP,
        "DispatchCommandSeparatorGroup",
        0,
        &ECOMSEP_HOOK,
    )
}

extern "C" fn hooked_eand(cmdnode: *mut CmdNode) -> i32 {
    handle_operator_group(cmdnode, "&&", ANDTYP, "DispatchAndGroup", 0, &EAND_HOOK)
}

extern "C" fn hooked_eor(cmdnode: *mut CmdNode) -> i32 {
    handle_operator_group(cmdnode, "||", ORTYP, "DispatchOrGroup", 0, &EOR_HOOK)
}

fn call_find_fix_and_run(cmdnode: *mut CmdNode) -> i32 {
    if let Some(hook) = FIND_FIX_AND_RUN_HOOK.get() {
        return unsafe { hook.call(cmdnode) };
    }

    0
}

fn call_dispatch(rio_type: i32, cmdnode: *mut CmdNode) -> i32 {
    if let Some(hook) = DISPATCH_HOOK.get() {
        return unsafe { hook.call(rio_type, cmdnode) };
    }

    0
}

fn handle_operator_group(
    cmdnode: *mut CmdNode,
    operator: &str,
    command_type: i32,
    source: &str,
    rio_type: i32,
    hook_storage: &'static OnceLock<GenericDetour<OperatorHandler>>,
) -> i32 {
    if !pipe_children_suppressed() {
        return match debugger::handle_operator_breakpoint(
            cmdnode,
            operator,
            command_type,
            source,
            Some(rio_type),
        ) {
            BreakOutcome::Continue => {
                with_pipe_children_suppressed(|| call_operator(cmdnode, hook_storage))
            }
            BreakOutcome::Skip(result) => result,
        };
    }

    call_operator(cmdnode, hook_storage)
}

fn call_operator(
    cmdnode: *mut CmdNode,
    hook_storage: &'static OnceLock<GenericDetour<OperatorHandler>>,
) -> i32 {
    if let Some(hook) = hook_storage.get() {
        return unsafe { hook.call(cmdnode) };
    }

    0
}

fn configured_rva(env_name: &str, fallback: usize) -> usize {
    std::env::var(env_name)
        .ok()
        .and_then(|value| parse_rva(&value))
        .unwrap_or(fallback)
}

fn parse_rva(value: &str) -> Option<usize> {
    let trimmed = value.trim();
    if let Some(hex) = trimmed
        .strip_prefix("0x")
        .or_else(|| trimmed.strip_prefix("0X"))
    {
        usize::from_str_radix(hex, 16).ok()
    } else {
        trimmed.parse().ok()
    }
}

fn pipe_children_suppressed() -> bool {
    SUPPRESS_PIPE_CHILDREN.with(|depth| depth.get() > 0)
}

fn with_pipe_children_suppressed<T>(callback: impl FnOnce() -> T) -> T {
    SUPPRESS_PIPE_CHILDREN.with(|depth| depth.set(depth.get() + 1));
    let result = callback();
    SUPPRESS_PIPE_CHILDREN.with(|depth| depth.set(depth.get().saturating_sub(1)));
    result
}
