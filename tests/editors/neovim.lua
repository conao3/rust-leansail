local config = vim.json.decode(table.concat(vim.fn.readfile(vim.env.LEANSAIL_EDITOR_CONFIG), "\n"))
local results = { version = tostring(vim.version()) }
local resume
local function wait(predicate)
	local deadline = vim.uv.now() + 30000
	while not predicate() do
		assert(vim.uv.now() < deadline, "エディタの応答待ちが期限を超えました")
		vim.defer_fn(resume, 10)
		coroutine.yield()
	end
end

local function run()
	vim.cmd.edit(vim.fn.fnameescape(config.file))
	vim.bo.filetype = "lean"
	local buffer = vim.api.nvim_get_current_buf()
	local original = vim.api.nvim_buf_get_lines(buffer, 0, -1, false)
	local client_id = assert(vim.lsp.start({
		name = "lean",
		cmd = config.command,
		root_dir = vim.fn.fnamemodify(config.file, ":h"),
		exit_timeout = 10000,
		flags = { debounce_text_changes = 10 },
	}))
	local client = assert(vim.lsp.get_client_by_id(client_id))
	wait(function()
		return client.initialized and vim.lsp.buf_is_attached(buffer, client_id)
	end)

	local function request(method, params)
		local response = assert(client:request_sync(method, params, 30000, buffer))
		assert(not response.err, vim.inspect(response.err))
		return response.result
	end

	local function ready()
		request("textDocument/waitForDiagnostics", {
			uri = vim.uri_from_bufnr(buffer),
			version = vim.lsp.util.buf_versions[buffer],
		})
	end

	local function errors()
		return vim.tbl_map(function(d)
			return d.message
		end, vim.diagnostic.get(buffer, { severity = vim.diagnostic.severity.ERROR }))
	end

	ready()
	wait(function()
		return #errors() == 2
	end)
	results.diagnostics = errors()
	vim.diagnostic.setloclist()
	assert(#vim.fn.getloclist(0) >= 2, "診断一覧を表示できません")
	vim.cmd.lclose()
	vim.api.nvim_win_set_cursor(0, { 2, 9 })
	vim.lsp.buf.hover()
	local hover
	wait(function()
		for _, win in ipairs(vim.api.nvim_list_wins()) do
			if vim.api.nvim_win_get_config(win).relative ~= "" then
				local text = table.concat(vim.api.nvim_buf_get_lines(vim.api.nvim_win_get_buf(win), 0, -1, false), "\n")
				if text:find("editorValue : Nat", 1, true) then
					hover = text
					vim.api.nvim_win_close(win, true)
					return true
				end
			end
		end
		return false
	end)
	results.hover = hover
	vim.lsp.buf.definition()
	wait(function()
		local pos = vim.api.nvim_win_get_cursor(0)
		return pos[1] == 1 and pos[2] == 4
	end)
	results.definition = { 0, 4 }

	vim.api.nvim_win_set_cursor(0, { 6, 12 })
	vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes("A<C-x><C-o>", true, false, true), "n", false)
	local items
	wait(function()
		items = vim.fn.complete_info({ "items" }).items
		return vim.iter(items):any(function(item)
			return item.word == "succ"
		end)
	end)
	results.completion = vim.tbl_map(function(item)
		return item.word
	end, items)
	vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes("<C-e><Esc>", true, false, true), "n", false)
	wait(function()
		return vim.api.nvim_get_mode().mode == "n"
	end)
	local goal = request("$/lean/plainGoal", {
		textDocument = { uri = vim.uri_from_bufnr(buffer) },
		position = { line = 3, character = 2 },
	})
	assert(goal.rendered:find("n = n", 1, true), vim.inspect(goal))
	local goal_buffer, goal_window = vim.lsp.util.open_floating_preview(vim.split(goal.rendered, "\n"), "markdown", {})
	assert(vim.api.nvim_win_is_valid(goal_window), "ゴールを表示できません")
	assert(table.concat(vim.api.nvim_buf_get_lines(goal_buffer, 0, -1, false), "\n"):find("n = n", 1, true))
	vim.api.nvim_win_close(goal_window, true)
	results.goal = goal

	local function edit(replacement, complete)
		vim.api.nvim_buf_set_text(buffer, 4, 20, 4, #vim.api.nvim_buf_get_lines(buffer, 4, 5, false)[1], { "" })
		for char in replacement:gmatch(".") do
			local line = vim.api.nvim_buf_get_lines(buffer, 4, 5, false)[1]
			vim.api.nvim_buf_set_text(buffer, 4, #line, 4, #line, { char })
			vim.wait(2)
		end
		if complete then
			vim.api.nvim_buf_set_lines(buffer, 5, 6, false, { "#check Nat.succ" })
		end
		ready()
	end

	edit("false", false)
	wait(function()
		return table.concat(errors(), "\n"):find("false", 1, true) ~= nil
	end)
	results.typing_diagnostics = errors()
	edit("42", true)
	wait(function()
		return #errors() == 0
	end)
	results.clean_diagnostics = errors()
	vim.bo.modified = false
	vim.cmd.bdelete()
	vim.cmd.edit(vim.fn.fnameescape(config.file))
	buffer = vim.api.nvim_get_current_buf()
	vim.bo.filetype = "lean"
	assert(vim.lsp.buf_attach_client(buffer, client_id))
	ready()
	wait(function()
		return #errors() == 2
	end)
	assert(vim.deep_equal(original, vim.api.nvim_buf_get_lines(buffer, 0, -1, false)))
	results.reopen_diagnostics = errors()
	vim.fn.writefile({ vim.json.encode(results) }, config.result)
end

resume = coroutine.wrap(function()
	local ok, error = xpcall(run, debug.traceback)
	if not ok then
		io.stderr:write(error .. "\n")
		vim.cmd.cquit()
	else
		vim.cmd.qall()
	end
end)
resume()
