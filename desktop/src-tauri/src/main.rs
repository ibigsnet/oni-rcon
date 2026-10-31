// The console window, without a terminal behind it on Windows.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    oni_rcon_lib::run()
}
