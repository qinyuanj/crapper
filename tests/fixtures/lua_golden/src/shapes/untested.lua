local M = {}

function M.never(x)
  if x then
    return 1
  elseif x == false then
    return 2
  end
  return 3
end

return M
