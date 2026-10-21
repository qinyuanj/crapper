local M = {}

local Account = {}
Account.__index = Account

local function helper(n)
  if n > 10 then
    return "big"
  end
  return "small"
end

function Account.new(balance)
  return setmetatable({ balance = balance or 0 }, Account)
end

function Account:deposit(amount)
  if amount <= 0 or amount ~= amount then
    error("invalid amount")
  end
  self.balance = self.balance + amount
  return self.balance
end

function M.sorted(xs, descending)
  local copy = { table.unpack(xs) }
  table.sort(copy, function(a, b)
    if descending then
      return a > b
    end
    return a < b
  end)
  return copy
end

function M.counter()
  local count = 0
  local function step(by)
    count = count + (by or 1)
    while count > 100 do
      count = count - 100
    end
    return count
  end
  return step
end

M.first, M.second = function(x)
  return x and x.first
end, function(x)
  if x then
    return x.second
  end
end

local tag, unused = function(value)
  return "<" .. value .. ">"
end

M.helper = helper
M.tag = tag
M.Account = Account

return M
