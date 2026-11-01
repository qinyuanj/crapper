from crapper.languages.typescript import functions_in_source


def test_function_class_method_and_arrow_are_separate_entries():
    source = """
export function choose(x: number): number {
  if (x > 0 && x < 10) return 1;
  for (let i = 0; i < x; i++) {
    if (i === 2) return i;
  }
  try {
    return x;
  } catch (e) {
    return 0;
  }
  return x > 0 ? 1 : 0;
}

export class Box {
  open(flag?: boolean): number {
    return flag ? 1 : 0;
  }
}

const arrow = (n: number) => (n > 0 ? n : 0);

function outer() {
  function inner(n: number) {
    if (n) return 1;
    return 0;
  }
  return inner(1);
}
"""
    functions = functions_in_source(source, "src/demo/box.ts", "/proj")
    assert [(fn.namespace, fn.name, fn.complexity) for fn in functions] == [
        ("demo.box", "choose", 7),
        ("demo.box.Box", "open", 2),
        ("demo.box", "arrow", 2),
        ("demo.box", "outer", 2),
    ]


def test_tsx_counts_jsx_logic():
    source = """
export function View(ok: boolean, ready: boolean) {
  return ok && ready ? 1 : 0;
}
"""
    functions = functions_in_source(source, "src/ui/view.tsx", "/proj")
    assert [(fn.namespace, fn.name, fn.complexity) for fn in functions] == [
        ("ui.view", "View", 3)
    ]


def test_source_root_is_stripped_without_a_src_directory():
    source = "export function view() { return 1 }\n"
    assert functions_in_source(source, "/proj/ui/view.tsx", "/proj")[0].namespace == "ui.view"
    assert functions_in_source(source, "/proj/ui/view.cts", "/proj/")[0].namespace == "ui.view"
    assert functions_in_source(source, "./ui/view.ts", ".")[0].namespace == "ui.view"
