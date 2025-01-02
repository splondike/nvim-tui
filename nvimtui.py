"""
Use NeoVim as a simple TUI with the help of a handler script.

Usage:
    nvimtui ./handler.sh

handler.sh should linewise read from stdin for events. It will get three lines in succession. The first is the event type, the second two are arguments.

It first gets ("url", "", "", ""), then ("<primary|secondary|something else>", "possible argument", "url of open buffer", "contents of line event was executed on")

The handler should return the action it wants to perform, then 0 or more arguments then '.END.' (without quotes) to indicate the end of its response.
Available actions shown in the program.

As an example, the following is a simple file system browser handler script:

    #!/bin/sh
    event=""
    arg1=""
    arg2=""
    arg3=""
    pos=0

    while read -r line;do
        if [ $pos -eq 0 ];then
            event="$line"
        elif [ $pos -eq 1 ];then
            arg1="$line"
        elif [ $pos -eq 2 ];then
            arg2="$line"
        else
            arg3="$line"
        fi
        pos=$(echo "($pos + 1) % 4" | bc)
        if [ ! $pos -eq 0 ];then
            continue
        fi

        if [ "$event" = "url" ];then
            echo "setbuffer"
            echo "text 1"
            if [ x"$arg1" = x"" ];then
                arg1="/"
            fi
            if [ -d "$arg1" ];then
                find "$arg1" -maxdepth 1
            else
                cat $arg1
            fi
        elif [ "$event" = "primary" ];then
            echo "seturl"
            echo "$arg3" | awk '{$1=$2="";print $0}'
        fi
        echo ".END."
    done
"""

import os
import subprocess
import sys
import time
import threading
from fcntl import fcntl, F_GETFL, F_SETFL
from os import O_NONBLOCK, read

import pynvim


URL_PREFIX = "nvimtui://"


def user_log(con, message):
    con.command(f"echo 'Nvimtui: {message}'")


def calculate_url(con):
    result = con.call("nvim_buf_get_name", 0)
    if result.startswith(URL_PREFIX):
        return result[len(URL_PREFIX):]
    else:
        return result


def handle_nvim_action(proc, con, args, state):
    user_log(con, "Calling subprocess")
    try:
        proc.stdin.write("\n".join(args) + "\n")
    except BrokenPipeError:
        user_log(con, "Subprocess closed unexpectedly\n" + proc.stderr.read())
        return

    args = []
    while True:
        line = proc.stdout.readline().strip()
        if line == ".END.":
            break
        args.append(line)

    error_output = b""
    try:
        while True:
            # Load up any error output
            error_output += read(proc.stderr.fileno(), 1024)
    except OSError:
        if len(error_output) > 0:
            con.lua.print(error_output.decode())
            user_log(con, "Error running subprocess, :messages for more")
        else:
            # This seems to be how you clear the statusline
            con.command("echo ''")

    if len(args) == 0:
        return

    action, *action_args = args

    handle_action(proc, con, action, action_args, state)


def handle_action(proc, con, action, action_args, state):
    target_window = state["window"]
    try:
        target_buffer = con.call("nvim_win_get_buf", target_window)
    except pynvim.api.common.NvimError:
        target_window = con.call("nvim_get_current_win")
        target_buffer = con.call("nvim_get_current_buf")

    def set_opt(name, value):
        con.call(
            "nvim_set_option_value",
            name,
            value,
            {"buf": target_buffer}
        )

    def edit_file(path):
        # Wasn't able to find a short and reliable way of editing a file in a
        # given buffer, so wrote this. But it has bugs; doesn't always add
        # to the recent buffers list. Was losing the contents of a buffer
        # on ',s'. So I'm leaving in most of my target_window code, but
        # it won't work currently.

        con.command("edit " + path)
        # target_buffer_number = -1
        # for buffer_number in con.call("nvim_list_bufs"):
        #     if con.call("nvim_buf_get_name", buffer_number) == path:
        #         target_buffer_number = buffer_number
        #         break
        #
        # if target_buffer_number != -1:
        #     con.call("nvim_win_set_buf", target_window, target_buffer_number)
        # else:
        #     con.call("nvim_buf_set_name", target_buffer, path)
        #     if not path.startswith(URL_PREFIX):
        #         with open(path) as fh:
        #             con.call(
        #                 "nvim_buf_set_lines",
        #                 target_buffer,
        #                 0,
        #                 -1,
        #                 False,
        #                 [
        #                     l.strip()
        #                     for l in fh.readlines()
        #                 ]
        #             )


    if action == "setbuffer":
        buf_properties = action_args[0].split(" ")

        set_opt("modifiable", True)
        con.call("nvim_buf_set_lines", target_buffer, 0, -1, False, action_args[1:])
        set_opt("modified", False)
        if len(buf_properties) > 0 and buf_properties[0] != "":
            set_opt("filetype", buf_properties[0])
        if len(buf_properties) > 1:
            if buf_properties[1] == "false":
                set_opt("modifiable", False)
        if len(buf_properties) > 2:
            con.call("nvim_win_set_cursor", target_window, [int(buf_properties[2]), 0])
    elif action == "seturl":
        edit_file(URL_PREFIX + action_args[0])
        set_opt("swapfile", False)
        set_opt("modified", False)
        handle_nvim_action(
            proc,
            con,
            ["url", action_args[0], calculate_url(con), ""],
            state
        )
    elif action == "setrawurl":
        edit_file(action_args[0])
        set_opt("swapfile", False)
        set_opt("modified", False)
    elif action == "noop":
        pass


def rpc_handler(socket_path):
    con = pynvim.attach("socket", path=socket_path)
    con.subscribe("NvimTuiEvent")

    handler_proc = subprocess.Popen(
        sys.argv[1:],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        text=True
    )
    fcntl(
        handler_proc.stderr,
        F_SETFL,
        fcntl(handler_proc.stderr, F_GETFL) | O_NONBLOCK
    )

    state = {
        "window": 0,
    }

    con.command(f"edit {URL_PREFIX}")
    con.command("set noswapfile")
    handle_nvim_action(
        handler_proc,
        con,
        ["url", "", calculate_url(con), ""],
        state
    )

    try:
        while True:
            msg = con.next_message()
            if msg is None:
                continue

            event_type, *event_args = msg.args

            if event_type == "UserAction":
                rpc_user_action(
                    handler_proc,
                    con,
                    event_args[0],
                    event_args[1] if len(event_args) > 1 else "",
                    state
                )
            elif event_type == "SetTargetWindow":
                sys.stderr.write(f"{event_args}")
                state["window"] = event_args[0]

    except EOFError:
        # Exit, neovim is closed
        pass


def rpc_user_action(handler_proc, con, event_name, event_arg, state):
    line_number = con.call("line", ".")
    column_number = con.call("col", ".")
    curr_mode = con.call("mode")
    if curr_mode == "n":
        # In normal mode just grab the line under the current cursor
        line = con.call("getline", ".").rstrip()
    else:
        # In visual mode, grab the selected text and separate lines by \n (not the newline char, but the encoding thereof).

        # These getpos calls return start and end cursor positions, not
        # the start and end of the text range
        pos1 = con.call("getpos", ".")
        pos2 = con.call("getpos", "v")
        if pos1[1] < pos2[1] or pos1[2] < pos2[2]:
            _, start_line, start_col, _ = pos1
            _, end_line, end_col, _ = pos2
        else:
            _, start_line, start_col, _ = pos2
            _, end_line, end_col, _ = pos1

        lines = con.call("getline", start_line, end_line)

        if curr_mode == "V":
            end_col = len(lines[-1])

        if start_line == end_line:
            lines[0] = lines[0][start_col-1:end_col]
        else:
            lines[0] = lines[0][start_col-1:]
            lines[-1] = lines[-1][:end_col]
        line = "\\n".join([l.rstrip() for l in lines])

    handle_nvim_action(
        handler_proc,
        con,
        [event_name, event_arg, calculate_url(con), f"{line_number} {column_number} {line}",],
        state
    )


def spawn_nvim():
    socket_path = "/tmp/nvim.sock"
    proc = subprocess.Popen(
        ["nvim", "--listen", socket_path, "-c", "NvimTuiEnable", "-c", "set nomodified", "-c", "lua vim.o.title=true ; vim.o.titlestring='NvimTui'"],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    for _ in range(50):
        if os.path.exists(socket_path):
            return proc, socket_path
        time.sleep(0.1)

    raise RuntimeError("Didn't find nvim socket")


def main():
    if len(sys.argv) < 2:
        print("Usage: nvimtui <handler_script.sh>")
        sys.exit(1)
    proc, socket_path = spawn_nvim()
    threading.Thread(target=rpc_handler, args=[socket_path], daemon=False).run()
    proc.wait()


if __name__ == "__main__":
    main()
