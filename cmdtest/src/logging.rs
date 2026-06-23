use std::fs::OpenOptions;
use std::io::Write;

pub fn log_json_entry(
    event_type: &str,
    command: Option<&str>,
    arguments: Option<&str>,
    command_type: Option<i32>,
    message: Option<&str>,
) {
    log_json_entry_with_extra(event_type, command, arguments, command_type, message, &[]);
}

pub fn log_json_entry_with_extra(
    event_type: &str,
    command: Option<&str>,
    arguments: Option<&str>,
    command_type: Option<i32>,
    message: Option<&str>,
    extra_fields: &[String],
) {
    let mut fields = Vec::new();
    fields.push(json_field("event_type", event_type));

    if let Some(command) = command {
        fields.push(json_field("command", command));
    }

    if let Some(arguments) = arguments {
        fields.push(json_field("arguments", arguments));
    }

    if let Some(command_type) = command_type {
        fields.push(format!("\"command_type\":{command_type}"));
    }

    if let Some(message) = message {
        fields.push(json_field("message", message));
    }

    fields.extend(extra_fields.iter().cloned());

    let line = format!("{{{}}}\n", fields.join(","));
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open("cmd_hook.json")
    {
        let _ = file.write_all(line.as_bytes());
        let _ = file.flush();
    }
}

pub fn json_field(name: &str, value: &str) -> String {
    format!("\"{}\":\"{}\"", name, escape_json(value))
}

fn escape_json(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len());
    for ch in value.chars() {
        match ch {
            '"' => escaped.push_str("\\\""),
            '\\' => escaped.push_str("\\\\"),
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            '\u{08}' => escaped.push_str("\\b"),
            '\u{0C}' => escaped.push_str("\\f"),
            ch if ch < ' ' => escaped.push_str(&format!("\\u{:04x}", ch as u32)),
            ch => escaped.push(ch),
        }
    }
    escaped
}
