-- Private sequence QA. Executes the exact preserved config.lua and cli.lua,
-- with pinned upstream hook.lua. Only loader commands and stand I/O are mocks.
local base = assert(arg[1], "source directory")
local candidate = assert(arg[2], "review candidate")
local realio = io
local emit = print
local selector = "/boot/nextboot.conf"
local include = "/boot/DaOS-nextbsd/once-review.lua"
local target = "disk0p1:/DaOS-nextbsd/BOOTAA64.EFI"
local marker = "disk0p1:/DaOS-recovery/normal.running"
local nonce = "review-6d8ddf2dd5774488983431c9a16b2a55"
local enabled = 'nextboot_enable="YES"\n' ..
    'autoboot_delay="NO"\nbeastie_disable="YES"\n' ..
    'daos_nextbsd_trial="' .. nonce .. '"\n' ..
    'exec="include ' .. include .. '"\n'
local consumed = enabled:gsub('^nextboot_enable="YES"', 'nextboot_enable="NO" ')
local CHAIN, RESET = {}, {}
local pass = 0

local function setup(options)
    options = options or {}
    local state = {env = {nextboot_conf = selector, ["efi-version"] = "2.70"},
        files = {[selector] = options.selector or enabled, [target] = "EFI-fixture",
            [marker] = string.char(1)},
        trace = {}, chains = 0, resets = 0, includes = 0}
    if options.env then
        for key, value in pairs(options.env) do
            state.env[key] = value
        end
    end
    if options.missing_efi then state.files[target] = nil end
    if options.marker ~= nil then state.files[marker] = options.marker end
    if options.missing_marker then state.files[marker] = nil end
    local function event(name) state.trace[#state.trace + 1] = name end
    loader = {
        getenv = function(key) return state.env[key] end,
        setenv = function(key, value) state.env[key] = value; return 0 end,
        unsetenv = function(key) state.env[key] = nil; return 0 end,
        parse = function(text)
            local words = {}
            for word in text:gmatch("%S+") do words[#words + 1] = word end
            return table.unpack(words)
        end,
    }
    function loader.command(command, value)
        if command == "include" then
            assert(value == include, "unexpected include")
            state.includes = state.includes + 1
            event("include-register")
            dofile(candidate)
            return 0
        elseif command == "chain" then
            assert(value == target, "unexpected chain")
            state.chains = state.chains + 1
            state.at_chain = state.files[selector]
            event("chain")
            if options.chain_returns then return options.chain_returns end
            error(CHAIN)
        elseif command == "reboot" then
            state.resets = state.resets + 1
            event("reboot")
            if options.reboot_returns then return 1 end
            error(RESET)
        end
        error("unexpected command " .. tostring(command))
    end
    loader.perform = function(text) return loader.command(loader.parse(text)) end
    io = {
        open = function(path, mode)
            if options.readback_missing and mode ~= "w" and state.write_attempted then
                return nil
            end
            if mode == "w" then
                state.write_attempted = true
                event("consume-open")
                if options.write_open_fails then return nil end
                return {path = path, mode = mode}
            end
            if state.files[path] == nil then return nil end
            return {path = path, mode = mode}
        end,
        read = function(file) return state.files[file.path] end,
        write = function(file, text)
            assert(file.path == selector and file.mode == "w")
            event("consume-write")
            if options.write_silent_failure then return nil end
            -- libsa does not truncate here: it overwrites the existing prefix.
            state.files[file.path] = text .. state.files[file.path]:sub(#text + 1)
            return true
        end,
        close = function(file) event("close-" .. file.path); return true end,
    }
    print = function(...) end
    package.loaded.hook = nil
    package.loaded.config = nil
    package.loaded.cli = nil
    package.loaded.core = {}
    package.preload.hook = function() return dofile(base .. "/hook.lua") end
    package.preload.config = function() return dofile(base .. "/config.lua") end
    package.preload.cli = function() return dofile(base .. "/cli.lua") end
    local config = require("config")
    require("cli")
    -- Main defaults and menu are outside the sequence under test. The exact
    -- config.parse, checkNextboot, config.load, CLI dispatch, and hook are used.
    config.readConf = function() end
    state.run = function() return pcall(config.load) end
    state.hooks = require("hook")
    return state
end

local function test(name, run)
    run()
    pass = pass + 1
    emit("PASS " .. name)
end

test("unsafe-direct-chain-precedes-consumption", function()
    local s = setup({selector = 'nextboot_enable="YES"\nexec="chain ' .. target .. '"\n'})
    local ok, why = s.run()
    assert(not ok and why == CHAIN)
    assert(s.chains == 1 and not s.write_attempted)
    assert(s.at_chain:match('^nextboot_enable="YES"'))
end)

test("deferred-chain-sees-exact-consumed-selector", function()
    local s = setup()
    local ok, why = s.run()
    assert(not ok and why == CHAIN)
    assert(s.includes == 1 and s.chains == 1 and s.resets == 0)
    assert(s.at_chain == consumed and s.env.nextboot_enable == "NO")
    local trace = table.concat(s.trace, "|")
    assert(trace:find("include%-register.*consume%-write.*chain"))
end)

test("second-boot-never-registers-or-chains", function()
    local s = setup({selector = consumed})
    assert(s.run())
    assert(s.includes == 0 and s.chains == 0 and not s.write_attempted)
end)

test("nvstore-yes-cannot-replay-consumed-selector", function()
    local s = setup({selector = consumed, env = {nextboot_enable = "YES"}})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.includes == 1 and s.chains == 0)
end)

test("write-open-failure-reboots-without-chain", function()
    local s = setup({write_open_fails = true})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0 and s.resets == 1)
    assert(s.files[selector] == enabled)
end)

test("silent-consume-failure-reboots-without-chain", function()
    local s = setup({write_silent_failure = true})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0 and s.resets == 1)
end)

test("missing-readback-reboots-without-chain", function()
    local s = setup({readback_missing = true})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0 and s.resets == 1)
end)

test("wrong-nonce-reboots-without-chain", function()
    local s = setup({selector = enabled:gsub('daos_nextbsd_trial="[^"]+"',
        'daos_nextbsd_trial="wrong-nonce"')})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0)
end)

test("non-uefi-reboots-without-chain", function()
    local s = setup()
    s.env["efi-version"] = nil
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0)
end)

test("unreadable-efi-reboots-after-consumption", function()
    local s = setup({missing_efi = true})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0 and s.files[selector] == consumed)
end)

test("missing-recovery-marker-refuses-chain", function()
    local s = setup({missing_marker = true})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0)
end)

test("zero-recovery-marker-refuses-chain", function()
    local s = setup({marker = string.char(0)})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0)
end)

test("invalid-recovery-marker-refuses-chain", function()
    local s = setup({marker = "1"})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0)
end)

test("chain-error-resets-instead-of-normal-fallthrough", function()
    local s = setup({chain_returns = 1})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 1 and s.resets == 1)
    assert(s.env.autoboot_delay == "NO" and s.files[selector] == consumed)
end)

test("returned-efi-also-resets", function()
    local s = setup({chain_returns = 0})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 1 and s.resets == 1)
end)

test("reset-return-leaves-c-autoboot-disabled", function()
    local s = setup({chain_returns = 1, reboot_returns = true})
    local ok, why = s.run()
    assert(not ok and why == "DAOS_NEXTBSD_RESET_RETURNED")
    assert(s.env.autoboot_delay == "NO" and s.chains == 1)
end)

test("same-hook-cannot-attempt-chain-twice", function()
    local s = setup({chain_returns = 1})
    s.run()
    pcall(s.hooks.runAll, "config.loaded")
    assert(s.chains == 1 and s.resets == 2)
end)

test("consumed-file-trailing-tamper-refuses-chain", function()
    local s = setup({selector = enabled .. 'unreviewed="YES"\n'})
    local ok, why = s.run()
    assert(not ok and why == RESET and s.chains == 0)
end)

io, print = realio, emit
emit("RESULT " .. pass .. " private sequence checks passed")
