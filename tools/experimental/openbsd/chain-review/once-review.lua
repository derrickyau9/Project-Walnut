-- OFFLINE REVIEW CANDIDATE ONLY. NOT INSTALLED OR AUTHORIZED FOR DEPLOYMENT.
-- The nonce, path, selected partition, and EFI payload remain provisional.
-- This file is included while parsing nextboot; it must only register a hook.
local hook = require("hook")
local nonce = "review-6d8ddf2dd5774488983431c9a16b2a55"
local selector = "/boot/nextboot.conf"
local include = "/boot/DaOS-nextbsd/once-review.lua"
local target = "disk0p1:/DaOS-nextbsd/BOOTAA64.EFI"
local marker = "disk0p1:/DaOS-recovery/normal.running"
local consumed = 'nextboot_enable="NO" \n' ..
    'autoboot_delay="NO"\n' ..
    'beastie_disable="YES"\n' ..
    'daos_nextbsd_trial="' .. nonce .. '"\n' ..
    'exec="include ' .. include .. '"\n'
local enabled = consumed:gsub('^nextboot_enable="NO" ', 'nextboot_enable="YES"')
local function read_text(path)
    local file = io.open(path)
    if file == nil then return nil end
    local text = io.read(file)
    io.close(file)
    return text
end
-- A persistent nvstore YES must not replay a selector already containing NO.
-- Capture the exact original YES content now; chain is still deferred.
local saw_enabled_selector = read_text(selector) == enabled
local attempted = false

local function stop(reason)
    -- Suppress the C-level autoboot fallback even if ResetSystem returns.
    loader.setenv("autoboot_delay", "NO")
    loader.setenv("beastie_disable", "YES")
    print("DAOS_NEXTBSD_REFUSED " .. reason)
    loader.perform("reboot")
    error("DAOS_NEXTBSD_RESET_RETURNED", 0)
end

hook.register("config.loaded", function()
    if attempted then
        return stop("already-attempted")
    end
    attempted = true
    if not saw_enabled_selector then
        return stop("selector-not-initially-enabled-exactly")
    end
    if loader.getenv("efi-version") == nil then
        return stop("not-uefi")
    end
    if loader.getenv("nextboot_conf") ~= selector or
        loader.getenv("nextboot_enable") ~= "NO" or
        loader.getenv("daos_nextbsd_trial") ~= nonce then
        return stop("selector-environment")
    end
    local text = read_text(selector)
    if text ~= consumed then
        return stop("selector-not-consumed-exactly")
    end
    local value = read_text(marker)
    if value ~= string.char(1) then
        return stop("recovery-marker-not-armed")
    end
    local image = io.open(target)
    if image == nil then
        return stop("efi-unreadable")
    end
    io.close(image)
    print("DAOS_NEXTBSD_SELECTOR_CONSUMED " .. nonce)
    -- The selected partition's EFI DeviceHandle is passed by command_chain.
    -- Do not supply EFI arguments: OpenBSD's own boot.conf controls bsd.rd.
    local status = loader.perform("chain " .. target)
    return stop("chain-returned-" .. tostring(status))
end)
