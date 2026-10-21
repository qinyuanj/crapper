-- Outside the configured ROOT list: a run that picks this up fails.
describe("outside", function()
  it("never runs", function() error("spec discovery ignored .busted ROOT") end)
end)
