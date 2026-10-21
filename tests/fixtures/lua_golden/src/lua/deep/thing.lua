local thing = {}

function thing.describe(x)
  return x == nil and "nothing" or tostring(x)
end

return thing
