use crate::cmd::{self, CmdNode};
use crate::logging::{json_field, log_json_entry, log_json_entry_with_extra};
use crate::win::is_safe_pointer;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread::sleep;
use std::time::{Duration, Instant};

static BREAK_ID: AtomicU64 = AtomicU64::new(1);

pub enum BreakOutcome {
    Continue,
    Skip(i32),
}

enum DebugDecision {
    Continue,
    Skip,
    Edit { command: String, arguments: String },
}

pub fn handle_command_breakpoint(
    cmdnode: *mut CmdNode,
    source: &str,
    rio_type: Option<i32>,
) -> BreakOutcome {
    if !is_safe_pointer(cmdnode.cast()) {
        return BreakOutcome::Continue;
    }

    let Some((command, arguments, command_type)) = cmd::command_parts(cmdnode) else {
        return BreakOutcome::Continue;
    };

    if command.is_empty() {
        return BreakOutcome::Continue;
    }

    if !debugger_enabled() {
        log_command_execution(&command, arguments.as_str(), command_type);
        return BreakOutcome::Continue;
    }

    match prompt_for_decision(&command, &arguments, command_type, source, rio_type) {
        DebugDecision::Continue => {
            log_command_execution(&command, arguments.as_str(), command_type);
            BreakOutcome::Continue
        }
        DebugDecision::Skip => {
            log_json_entry_with_extra(
                "command_skipped",
                Some(&command),
                (!arguments.is_empty()).then_some(arguments.as_str()),
                Some(command_type),
                Some("Command skipped by debugger"),
                &debug_extra_fields(BREAK_ID.load(Ordering::Relaxed) - 1, source, rio_type),
            );
            BreakOutcome::Skip(0)
        }
        DebugDecision::Edit {
            command: edited_command,
            arguments: edited_arguments,
        } => {
            unsafe {
                cmd::set_node_strings(&mut *cmdnode, &edited_command, &edited_arguments);
            }
            log_command_execution(&edited_command, edited_arguments.as_str(), command_type);
            BreakOutcome::Continue
        }
    }
}

pub fn handle_operator_breakpoint(
    cmdnode: *mut CmdNode,
    operator: &str,
    command_type: i32,
    source: &str,
    rio_type: Option<i32>,
) -> BreakOutcome {
    if !is_safe_pointer(cmdnode.cast()) {
        return BreakOutcome::Continue;
    }

    let command = cmd::render_operator_node(cmdnode, operator).unwrap_or_default();
    if command.is_empty() {
        log_json_entry_with_extra(
            "operator_render_error",
            None,
            None,
            Some(command_type),
            Some(&format!(
                "{source} was hit, but the operator node could not be rendered; {}",
                cmd::describe_pointer_slots(cmdnode)
            )),
            &debug_extra_fields(0, source, rio_type),
        );
        return BreakOutcome::Continue;
    }

    if !debugger_enabled() {
        log_command_execution(&command, "", command_type);
        return BreakOutcome::Continue;
    }

    match prompt_for_decision(&command, "", command_type, source, rio_type) {
        DebugDecision::Skip => {
            log_json_entry_with_extra(
                "command_skipped",
                Some(&command),
                None,
                Some(command_type),
                Some("Operator group skipped by debugger"),
                &debug_extra_fields(BREAK_ID.load(Ordering::Relaxed) - 1, source, rio_type),
            );
            BreakOutcome::Skip(0)
        }
        DebugDecision::Continue | DebugDecision::Edit { .. } => {
            log_command_execution(&command, "", command_type);
            BreakOutcome::Continue
        }
    }
}

fn prompt_for_decision(
    command: &str,
    arguments: &str,
    command_type: i32,
    source: &str,
    rio_type: Option<i32>,
) -> DebugDecision {
    let break_id = BREAK_ID.fetch_add(1, Ordering::Relaxed);
    log_json_entry_with_extra(
        "debug_break",
        Some(command),
        (!arguments.is_empty()).then_some(arguments),
        Some(command_type),
        Some("Paused before command execution"),
        &debug_extra_fields(break_id, source, rio_type),
    );

    let decision = wait_for_debug_decision(break_id);
    match &decision {
        DebugDecision::Continue => log_debug_decision(break_id, "continue", command, arguments),
        DebugDecision::Skip => log_debug_decision(break_id, "skip", command, arguments),
        DebugDecision::Edit {
            command: edited_command,
            arguments: edited_arguments,
        } => log_debug_decision(break_id, "edit", edited_command, edited_arguments),
    }

    decision
}

fn log_command_execution(command: &str, arguments: &str, command_type: i32) {
    log_json_entry(
        "command_execution",
        Some(command),
        (!arguments.is_empty()).then_some(arguments),
        Some(command_type),
        None,
    );
}

fn log_debug_decision(break_id: u64, action: &str, command: &str, arguments: &str) {
    log_json_entry_with_extra(
        "debug_decision",
        Some(command),
        (!arguments.is_empty()).then_some(arguments),
        None,
        Some(action),
        &[format!("\"break_id\":{break_id}")],
    );
}

fn debug_extra_fields(break_id: u64, source: &str, rio_type: Option<i32>) -> Vec<String> {
    let mut fields = vec![
        format!("\"break_id\":{break_id}"),
        json_field("source", source),
    ];
    if let Some(rio_type) = rio_type {
        fields.push(format!("\"rio_type\":{rio_type}"));
    }
    fields
}

fn debugger_enabled() -> bool {
    std::env::var("EXORCISM_DEBUGGER")
        .map(|value| {
            matches!(
                value.to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false)
}

fn debug_response_path(break_id: u64) -> Option<PathBuf> {
    std::env::var("EXORCISM_DEBUG_DIR")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(|dir| PathBuf::from(dir).join(format!("response_{break_id}.txt")))
}

fn debug_timeout() -> Duration {
    let millis = std::env::var("EXORCISM_DEBUG_TIMEOUT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(300_000);
    Duration::from_millis(millis)
}

fn wait_for_debug_decision(break_id: u64) -> DebugDecision {
    let Some(path) = debug_response_path(break_id) else {
        return DebugDecision::Continue;
    };

    let started = Instant::now();
    while started.elapsed() < debug_timeout() {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Some(decision) = parse_debug_decision(&content) {
                let _ = std::fs::remove_file(path);
                return decision;
            }
        }

        sleep(Duration::from_millis(50));
    }

    log_json_entry_with_extra(
        "debug_timeout",
        None,
        None,
        None,
        Some("Debugger did not respond before timeout; continuing command"),
        &[format!("\"break_id\":{break_id}")],
    );
    DebugDecision::Continue
}

fn parse_debug_decision(content: &str) -> Option<DebugDecision> {
    let mut lines = content.lines();
    let action = lines.next()?.trim().to_ascii_lowercase();
    match action.as_str() {
        "continue" => Some(DebugDecision::Continue),
        "skip" => Some(DebugDecision::Skip),
        "edit" => {
            let command = lines.next().unwrap_or_default().to_string();
            let arguments = lines.collect::<Vec<_>>().join("\n");
            if command.trim().is_empty() {
                Some(DebugDecision::Continue)
            } else {
                Some(DebugDecision::Edit { command, arguments })
            }
        }
        _ => None,
    }
}
