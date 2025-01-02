local function send_nvim_tui_event(name)
	vim.rpcnotify(0, "NvimTuiEvent", "UserAction", name)
end

local function setup_nvim_tui()
	local function send_message(name)
		return function()
			send_nvim_tui_event(name)
		end
	end

	vim.keymap.set("", "<localleader><localleader>", send_message("primary"))
	vim.keymap.set("", "<localleader>n", send_message("secondary"))
	vim.keymap.set("", "<localleader>e", send_message("tertiary"))
	vim.keymap.set("", "<localleader>i", send_message("opt_four"))
	vim.keymap.set("", "<localleader>o", send_message("opt_five"))
	vim.keymap.set("", "<C-c>", function()
		vim.cmd.quitall()
	end)
end

vim.api.nvim_create_user_command("NvimTuiEnable", setup_nvim_tui, {})
vim.api.nvim_create_user_command("NvimTuiSendEvent", function(opts)
	send_nvim_tui_event(opts.fargs[1])
end, { nargs = 1 })
vim.api.nvim_create_user_command("NvimTuiSetTargetWindow", function(opts)
	local window = vim.api.nvim_get_current_win()
	if opts.fargs[1] ~= nil then
		window = opts.fargs[1]
	end
	vim.rpcnotify(0, "NvimTuiEvent", "SetTargetWindow", window)
end, { nargs = "?" })

vim.api.nvim_create_augroup("NvimTuiCursorMovedDebounce", { clear = true })
local debounce_time = 300 -- ms (300 milliseconds)
local debounce_timer = nil
local debounce_action = "primary"
local debounce_target_window = nil
local debounce_target_buffer = nil
local cursor_move_autocmd = nil

local function on_cursor_moved()
	if debounce_timer then
		debounce_timer:stop()
	end

	local curr_win = vim.api.nvim_get_current_win()
	local curr_buf = vim.api.nvim_get_current_buf()
	if curr_win ~= debounce_target_window or curr_buf ~= debounce_target_buffer then
		return
	end

	debounce_timer = vim.defer_fn(function()
		send_nvim_tui_event(debounce_action)
	end, debounce_time)
end

vim.api.nvim_create_user_command("NvimTuiWatchCursorMove", function(opts)
	debounce_target_window = vim.api.nvim_get_current_win()
	debounce_target_buffer = vim.api.nvim_get_current_buf()

	if opts.fargs[1] then
		if cursor_move_autocmd == nil then
			cursor_move_autocmd = vim.api.nvim_create_autocmd("CursorMoved", {
				group = "NvimTuiCursorMovedDebounce",
				callback = on_cursor_moved,
			})
		end
		debounce_action = opts.fargs[1]
	else
		if cursor_move_autocmd ~= nil then
			vim.api.nvim_del_autocmd(cursor_move_autocmd)
		end
	end
end, { nargs = "?" })
