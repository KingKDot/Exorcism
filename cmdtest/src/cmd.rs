use crate::win::{is_safe_pointer, safe_extract_wide_string, Handle};
use std::ptr::null_mut;
use std::sync::{Mutex, OnceLock};

pub const CMDTYP: i32 = 0;
pub const CSTYP: i32 = 44;
pub const ORTYP: i32 = 45;
pub const ANDTYP: i32 = 46;
pub const PIPTYP: i32 = 47;
pub const RIO_PIPE: i32 = 1;

static EDITED_STRINGS: OnceLock<Mutex<Vec<Box<[u16]>>>> = OnceLock::new();

#[repr(C)]
pub struct SavType {
    saveptrs: [*mut u16; 12],
}

#[repr(C)]
pub struct Relem {
    rdhndl: Handle,
    fname: *mut u16,
    svhndl: Handle,
    flag: i32,
    rdop: u16,
    nxt: *mut Relem,
}

#[repr(C)]
pub struct CmdNode {
    pub command_type: i32,
    save: SavType,
    rio: *mut Relem,
    pub cmdline: *mut u16,
    pub argptr: *mut u16,
    flag: i32,
    cmdarg: i32,
}

#[repr(C)]
pub struct OpNode {
    pub command_type: i32,
    save: SavType,
    rio: *mut Relem,
    pub lhs: *mut CmdNode,
    pub rhs: *mut CmdNode,
    extra: [i32; 4],
}

pub fn command_parts(node: *mut CmdNode) -> Option<(String, String, i32)> {
    if !is_safe_pointer(node.cast()) {
        return None;
    }

    let node = unsafe { &*node };
    let command = safe_extract_wide_string(node.cmdline, 500);
    let arguments = safe_extract_wide_string(node.argptr, 500);
    Some((command, arguments, node.command_type))
}

pub fn render_cmd_node(node: *mut CmdNode) -> Option<String> {
    let (command, arguments, _) = command_parts(node)?;
    Some(join_command(&command, &arguments))
}

pub fn render_operator_node(node: *mut CmdNode, operator: &str) -> Option<String> {
    let (lhs_node, rhs_node) = find_operator_children(node, 0)?;
    let lhs = render_node_inner(lhs_node, 1).unwrap_or_default();
    let rhs = render_node_inner(rhs_node, 1).unwrap_or_default();
    if lhs.trim().is_empty() || rhs.trim().is_empty() {
        None
    } else {
        Some(format!("{} {} {}", lhs.trim(), operator, rhs.trim()))
    }
}

pub fn describe_pointer_slots(node: *mut CmdNode) -> String {
    if !is_safe_pointer(node.cast()) {
        return "node pointer is not readable".to_string();
    }

    let words = node.cast::<usize>();
    let mut slots = Vec::new();
    for index in 1..64 {
        let candidate = unsafe { *words.add(index) as *mut CmdNode };
        if !is_safe_pointer(candidate.cast()) {
            continue;
        }

        let command_type = unsafe { (*candidate).command_type };
        if (0..=80).contains(&command_type) {
            let preview = render_cmd_node(candidate).unwrap_or_default();
            slots.push(format!(
                "{}:0x{:X}:type={}:{}",
                index, candidate as usize, command_type, preview
            ));
        }
    }

    if slots.is_empty() {
        "no plausible node pointers found".to_string()
    } else {
        slots.join("; ")
    }
}

fn render_node_inner(node: *mut CmdNode, depth: usize) -> Option<String> {
    if depth > 12 || !is_safe_pointer(node.cast()) {
        return None;
    }

    let command_type = unsafe { (*node).command_type };
    match command_type {
        CMDTYP => render_cmd_node(node),
        PIPTYP | CSTYP | ORTYP | ANDTYP => {
            let (lhs_node, rhs_node) = find_operator_children(node, depth)?;
            let lhs = render_node_inner(lhs_node, depth + 1).unwrap_or_default();
            let rhs = render_node_inner(rhs_node, depth + 1).unwrap_or_default();
            let op = match command_type {
                PIPTYP => "|",
                CSTYP => "&",
                ORTYP => "||",
                ANDTYP => "&&",
                _ => "",
            };

            Some(format!("{} {} {}", lhs.trim(), op, rhs.trim()))
        }
        _ => render_cmd_node(node),
    }
}

fn find_operator_children(
    node: *mut CmdNode,
    depth: usize,
) -> Option<(*mut CmdNode, *mut CmdNode)> {
    let op_node = node.cast::<OpNode>();
    if is_safe_pointer(op_node.cast()) {
        let op_node = unsafe { &*op_node };
        if valid_node(op_node.lhs) && valid_node(op_node.rhs) {
            return Some((op_node.lhs, op_node.rhs));
        }
    }

    let words = node.cast::<usize>();
    for index in 1..32 {
        let lhs = unsafe { *words.add(index) as *mut CmdNode };
        let rhs = unsafe { *words.add(index + 1) as *mut CmdNode };
        if lhs == node || rhs == node || lhs == rhs {
            continue;
        }

        if !valid_node(lhs) || !valid_node(rhs) {
            continue;
        }

        let lhs_rendered = render_node_inner(lhs, depth + 1).unwrap_or_default();
        let rhs_rendered = render_node_inner(rhs, depth + 1).unwrap_or_default();
        if !lhs_rendered.trim().is_empty() && !rhs_rendered.trim().is_empty() {
            return Some((lhs, rhs));
        }
    }

    None
}

fn valid_node(node: *mut CmdNode) -> bool {
    if !is_safe_pointer(node.cast()) {
        return false;
    }

    let command_type = unsafe { (*node).command_type };
    (0..=57).contains(&command_type)
}

fn join_command(command: &str, arguments: &str) -> String {
    if arguments.is_empty() {
        command.to_string()
    } else {
        format!("{} {}", command, arguments.trim())
    }
}

pub unsafe fn set_node_strings(node: &mut CmdNode, command: &str, arguments: &str) {
    node.cmdline = retain_wide_string(command);
    node.argptr = retain_wide_string(&normalize_arguments(arguments));
}

fn normalize_arguments(arguments: &str) -> String {
    if arguments.is_empty() || arguments.starts_with(' ') || arguments.starts_with('\t') {
        arguments.to_string()
    } else {
        format!(" {arguments}")
    }
}

fn retain_wide_string(value: &str) -> *mut u16 {
    let mut wide: Vec<u16> = value.encode_utf16().collect();
    wide.push(0);
    let boxed = wide.into_boxed_slice();
    let ptr = boxed.as_ptr() as *mut u16;
    let storage = EDITED_STRINGS.get_or_init(|| Mutex::new(Vec::new()));
    if let Ok(mut strings) = storage.lock() {
        strings.push(boxed);
        ptr
    } else {
        null_mut()
    }
}
