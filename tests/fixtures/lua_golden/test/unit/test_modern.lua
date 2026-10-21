local modern = require("shapes.modern")

describe("modern", function()
  it("does bit work", function()
    local both, either, diff = modern.bits(6, 3)
    assert.equal(2, both)
    assert.equal(7, either)
    assert.equal(5, diff)
  end)

  it("finds", function()
    assert.equal(2, modern.find({ 1, 2 }, 2))
  end)

  it("closes", function()
    local log = {}
    assert.is_true(modern.closing(log))
    assert.same({ "body", "closed" }, log)
  end)
end)
