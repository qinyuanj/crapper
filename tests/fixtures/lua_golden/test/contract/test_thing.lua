local thing = require("deep.thing")

describe("thing", function()
  it("describes", function()
    assert.equal("nothing", thing.describe(nil))
    assert.equal("4", thing.describe(4))
  end)
end)
