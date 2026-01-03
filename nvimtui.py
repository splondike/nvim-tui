"""
Use NeoVim as a simple TUI with the help of a handler script.

Usage:
    nvimtui ./handler.py

    or (useful for debugging):

    nvim --listen /my/path.sock
    nvimtui --socket /my/path.sock ./handler.py

handler.py will receive events from neovim on stdin. These will be formatted as line-delimited JSON. The handler should return a JSON array of actions.

Run `nvimtui ./example-script.sh` for an example.
"""

import argparse
import json
import logging
import os
import select
import subprocess
import sys
import tempfile
import time
from typing import Optional
from fcntl import fcntl, F_GETFL, F_SETFL
from os import O_NONBLOCK

import pynvim


URL_PREFIX = "nvimtui://"
logger = logging.getLogger(__name__)


def calculate_url(con):
    result = con.call("nvim_buf_get_name", 0)
    if result.startswith(URL_PREFIX):
        return result[len(URL_PREFIX):]
    else:
        return result


def kitty_cmd(cmd, *args):
    result = subprocess.run(
        ["kitty", "@", cmd, *args],
        capture_output=True,
        check=True
    )
    return result.stdout


def preview_ensure_window_exists(handle: Optional[str]) -> str:
    """
    Pop open a window to show rendered images
    """

    if preview_window_handle_valid(handle):
        return handle
    else:
        curr_window_id = [
            window["id"]
            for os_window in json.loads(kitty_cmd("ls"))
            for tab in os_window["tabs"]
            for window in tab["windows"]
            if tab["is_active"] and window["is_focused"]
        ][0]
        photo_window_id = kitty_cmd(
            "launch",
            "bash",
            "-c",
            "while read -s file;do tpreview \"$file\" --place $(tput cols)x$(tput lines)@0x0 --image-id=1;done"
        ).decode()
        kitty_cmd("focus-window", "--match", f"id:{curr_window_id}")
        return photo_window_id.strip()


def preview_window_handle_valid(handle: Optional[str]) -> bool:
    """
    Does the given preview window still exist?
    """

    if handle is None:
        return False

    return handle in [
        str(window["id"])
        for os_window in json.loads(kitty_cmd("ls"))
        for tab in os_window["tabs"]
        for window in tab["windows"]
    ]


def preview_view_file(handle: str, filepath: str):
    """
    Shows the given file in the preview window
    """

    kitty_cmd(
        "send-text",
        "--match",
        f"id:{handle}",
        filepath + "\n"
    )


def preview_close_window(handle: Optional[str]):
    if handle is not None:
        kitty_cmd(
          "close-window",
          "--match",
          f"id:{handle}"
        )


def handle_nvim_action(proc, con, args, state):
    """
    Ask the process what to do in response to something that happens in nvim.
    """

    try:
        proc.stdin.write(json.dumps(args) + "\n")
    except BrokenPipeError:
        logger.error("Subprocess closed unexpectedly")
        return False

    line = ""
    stderr = ""
    while ("\n" not in line and proc.poll() is None):
        try:
            ready, _, _ = select.select([proc.stdout, proc.stderr], [], [], 1)
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            return False
            break
        if proc.stderr in ready:
            err = proc.stderr.read()
            sys.stderr.write(err)
            stderr += err
        if proc.stdout in ready:
            newline = False
            while c := proc.stdout.read(1):
                if c == "\n":
                    newline = True
                    break
                else:
                    line += c
            if newline:
                break

    maybe_returncode = proc.poll()
    if maybe_returncode is None:
        try:
            args = json.loads(line)
        except json.decoder.JSONDecodeError:
            print("Error decoding program output, got:", file=sys.stderr)
            print(line, file=sys.stderr)
            return False

        for action in args["actions"]:
            handle_script_action(
                proc,
                con,
                action["action"],
                action["args"],
                state
            )
        return True
    elif maybe_returncode == 0:
        logger.info("Backing process exited normally")
        return False
    else:
        stderr += proc.stderr.read()
        logger.error(f"Backing process exited with errors: {stderr}")
        return False


def handle_script_action(proc, con, action, action_args, state):
    """
    Update Neovim based on the commands the script has sent
    """

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
        con.call(
            "nvim_buf_set_lines",
            target_buffer,
            0,
            -1,
            False,
            action_args[1].split("\n")
        )
        set_opt("modified", False)
        if len(buf_properties) > 0 and buf_properties[0] != "":
            set_opt("filetype", buf_properties[0])
        if len(buf_properties) > 1:
            if buf_properties[1] == "false":
                set_opt("modifiable", False)
        if len(buf_properties) > 2:
            con.call(
                "nvim_win_set_cursor",
                target_window,
                [int(buf_properties[2]), 0]
            )
    elif action == "seturl":
        edit_file(URL_PREFIX + action_args[0])
        set_opt("swapfile", False)
        set_opt("modified", False)
        handle_nvim_action(
            proc,
            con,
            {
                "action": "url",
                "args": [action_args[0]],
                "url": calculate_url(con)
            },
            state
        )
    elif action == "setrawurl":
        edit_file(action_args[0])
        set_opt("swapfile", False)
        set_opt("modified", False)
    elif action == "lua_function":
        con.exec_lua(
            *action_args
        )
    elif action == "preview_file":
        state["preview_handle"] = preview_ensure_window_exists(state.get("preview_handle"))
        preview_view_file(state["preview_handle"], action_args[0])
    elif action == "noop":
        pass


def rpc_handler(socket_path, script_path, script_args) -> int:
    con = pynvim.attach("socket", path=socket_path)

    class VimLoggingHandler(logging.Handler):
        def emit(self, record):
            con.lua.print(f"Nvimtui: {record.message}")

    logger.addHandler(VimLoggingHandler())

    con.subscribe("NvimTuiEvent")

    handler_proc = subprocess.Popen(
        [script_path, *script_args],
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
    # Clear the statusline too, nice when debugging to see if there are errors
    con.command("echo ''")
    should_continue = handle_nvim_action(
        handler_proc,
        con,
        {
            "action": "url",
            "args": [""],
            "url": calculate_url(con)
        },
        state
    )
    if not should_continue:
        preview_close_window(state["preview_handle"])
        return 1

    try:
        while True:
            msg = con.next_message()
            if msg is None:
                continue

            event_type, *event_args = msg.args

            if event_type == "UserAction":
                should_continue = rpc_user_action(
                    handler_proc,
                    con,
                    event_args[0],
                    event_args[1] if len(event_args) > 1 else "",
                    state
                )
                if not should_continue:
                    # The backing process exited, close nvimtui
                    preview_close_window(state["preview_handle"])
                    return 1
            elif event_type == "SetTargetWindow":
                sys.stderr.write(f"{event_args}")
                state["window"] = event_args[0]

    except EOFError:
        # Exit, neovim is closed, but give the backing process a
        # chance to cleanup first
        handle_nvim_action(
            handler_proc,
            con,
            {
                "action": "shutdown",
                "args": [],
                "url": "",
            },
            state
        )
    preview_close_window(state["preview_handle"])
    return 0


def rpc_user_action(
    handler_proc,
    con,
    event_name: str,
    event_arg: str,
    state: dict
) -> bool:
    line_number = con.call("line", ".")
    column_number = con.call("col", ".")
    curr_mode = con.call("mode")
    if curr_mode == "n":
        # In normal mode just grab the line under the current cursor
        selection = con.call("getline", ".").rstrip()
    else:
        # In visual mode, grab the selected text and separate lines by \n
        # (not the newline char, but the encoding thereof).

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
        selection = "\\n".join([line.rstrip() for line in lines])

    buffer_number = con.call("bufnr")
    return handle_nvim_action(
        handler_proc,
        con,
        {
            "action": event_name,
            "args": [event_arg],
            "url": calculate_url(con),
            "buffer_number": buffer_number,
            "line_number": line_number,
            "column_number": column_number,
            "selection_text": selection
        },
        state
    )


def spawn_nvim():
    socket_path = tempfile.mktemp(prefix="nvimtui_")
    subprocess.Popen(
        [
            "nvim",
            "--listen",
            socket_path,
            "-c",
            "NvimTui start",
            "-c",
            "set nomodified",
            "-c",
            "lua vim.o.title=true ; vim.o.titlestring='NvimTui'"
        ],
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    for _ in range(50):
        if os.path.exists(socket_path):
            return socket_path
        time.sleep(0.1)

    raise RuntimeError("Didn't find nvim socket")


def main():
    parser = argparse.ArgumentParser(
        description=globals()["__doc__"]
    )
    parser.add_argument("script", help="Script to run")
    parser.add_argument("args", nargs="*", help="Arguments to script")
    parser.add_argument(
        "--socket",
        help="Don't spawn a new nvim instance, use this existing socket"
    )
    args = parser.parse_args()
    if not args.script:
        parser.print_usage()
        sys.exit(1)

    logger.setLevel(logging.DEBUG)

    socket = args.socket
    if args.socket is None:
        socket = spawn_nvim()
    else:
        console_handler = logging.StreamHandler(sys.stderr)
        console_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    sys.exit(rpc_handler(socket, args.script, args.args))


if __name__ == "__main__":
    main()
