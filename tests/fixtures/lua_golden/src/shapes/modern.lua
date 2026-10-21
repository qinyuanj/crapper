local M = {}

local LIMIT <const> = 8

function M.bits(a, b)
  local both = a & b
  local either = a | b
  local diff = a ~ b
  return both, either, diff, (a << 2) >> 1, ~a
end

function M.halves(n)
  return n // 2, n % 2
end

function M.find(xs, wanted)
  for i = 1, #xs do
    if xs[i] == wanted then
      goto found
    end
  end
  do return nil end
  ::found::
  return wanted
end

function M.closing(log)
  local handle <close> = setmetatable({}, {
    __close = function()
      log[#log + 1] = "closed"
    end,
  })
  repeat
    log[#log + 1] = "body"
  until #log >= 1 or LIMIT < 0
  return handle ~= nil
end

M.walk = function(t)
  local total = 0
  for _, v in pairs(t) do
    total = total + (type(v) == "number" and v or 0)
  end
  return total
end

return M
