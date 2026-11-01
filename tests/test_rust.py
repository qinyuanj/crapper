from crapper.languages.rust import functions_in_source


def _write_manifest(tmp_path, name="demo"):
    (tmp_path / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

SOURCE = """
pub fn choose(x: i32) -> i32 {
    if x > 0 && x < 10 {
        return 1;
    }
    match x {
        1 => 1,
        2 => 2,
        _ => 0,
    }
}

impl Sample {
    pub fn run(&self) -> i32 {
        if self.ok { 1 } else { 0 }
    }
}

impl Trait for Sample {
    fn draw(&mut self) {}
}

mod tests {
    fn hidden() {
        if true {}
    }
}

fn outer() {
    fn inner() {
        if true {}
    }
}
"""


def test_functions_methods_and_skipped_tests(tmp_path):
    _write_manifest(tmp_path, "demo-game")
    path = tmp_path / "src" / "lib.rs"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")
    functions = functions_in_source(SOURCE, "src/lib.rs", str(tmp_path))
    assert [(fn.namespace, fn.name, fn.complexity) for fn in functions] == [
        ("demo_game", "choose", 6),
        ("demo_game::Sample", "run", 2),
        ("demo_game::Sample", "draw", 1),
        ("demo_game", "outer", 2),
    ]


def test_impl_for_a_nested_generic_type(tmp_path):
    _write_manifest(tmp_path)
    source = "impl Trait for Vec<crate::Sample> {\n    fn draw(&self) {}\n}\n"
    functions = functions_in_source(source, str(tmp_path / "src" / "lib.rs"), str(tmp_path))
    assert functions[0].name == "draw"
    assert functions[0].namespace.endswith("Vec") or "Sample" in functions[0].namespace


def test_module_paths_outside_the_usual_layout(tmp_path):
    _write_manifest(tmp_path)
    mod = "pub fn open() {}\n"
    binary = "pub fn main_ok() {}\n"
    functions_in_source(mod, str(tmp_path / "src" / "game" / "mod.rs"), str(tmp_path))
    functions = functions_in_source(binary, str(tmp_path / "src" / "bin" / "tool.rs"), str(tmp_path))
    assert functions[0].namespace == "tool"
    outside = functions_in_source("pub fn loose() {}\n", "/tmp/loose.rs", str(tmp_path))
    assert outside[0].name == "loose"
    bare = functions_in_source("pub fn bare() {}\n", str(tmp_path / "notes.rs"), None)
    assert bare[0].name == "bare"


def test_nested_module_path(tmp_path):
    _write_manifest(tmp_path)
    path = tmp_path / "src" / "game" / "board.rs"
    path.parent.mkdir(parents=True)
    source = "pub fn place() -> i32 { if ready { 1 } else { 0 } }\n"
    path.write_text(source, encoding="utf-8")
    functions = functions_in_source(source, "src/game/board.rs", str(tmp_path))
    assert [(fn.namespace, fn.name, fn.complexity) for fn in functions] == [
        ("demo::game::board", "place", 2)
    ]


def test_cfg_test_items_are_test_code(tmp_path):
    _write_manifest(tmp_path)
    source = """
pub fn kept() {}

#[cfg(test)]
pub fn helper() { if true {} }

#[cfg(test)]
mod policy_shape_tests {
    fn case() {}
}

#[cfg(test)]
#[allow(dead_code)]
impl Host {
    pub fn fake() {}
}

impl Host {
    pub fn real() {}
    #[cfg(test)]
    pub fn probe() {}
    #[test]
    fn checks() {}
}

#[cfg(all(test, unix))]
fn unix_only() {}

#[cfg(all(test, not(miri)))]
fn not_under_miri() {}

#[tokio::test]
async fn async_case() {}

#[cfg(not(test))]
fn shipped() {}

#[cfg(any(test, feature = "fixtures"))]
pub fn fixture() {}

#[cfg(feature = "test")]
pub fn feature_named_test() {}

mod support {
    #![cfg(test)]
    fn inner() {}
}
"""
    functions = functions_in_source(source, str(tmp_path / "src" / "lib.rs"), str(tmp_path))
    assert [fn.name for fn in functions] == [
        "kept", "real", "shipped", "fixture", "feature_named_test"
    ]


def test_a_tests_file_module_is_test_code(tmp_path):
    _write_manifest(tmp_path)
    path = tmp_path / "src" / "compose" / "tests.rs"
    assert functions_in_source("fn case() {}\n", str(path), str(tmp_path)) == []
    inner = "#![cfg(test)]\nfn case() {}\n"
    assert functions_in_source(inner, str(tmp_path / "src" / "support.rs"), str(tmp_path)) == []


def test_a_crate_named_tests_is_not_test_code(tmp_path):
    _write_manifest(tmp_path, "tests")
    functions = functions_in_source("pub fn run() {}\n", str(tmp_path / "src" / "lib.rs"), str(tmp_path))
    assert [fn.name for fn in functions] == ["run"]


def _crate(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\nname = "other"\n',
        encoding="utf-8",
    )
    return str(tmp_path / "src" / "lib.rs")


def test_impl_namespace_is_the_self_type_name(tmp_path):
    path = _crate(tmp_path)
    source = (
        "impl<T> Widget<T> { fn a(&self) {} }\n"
        "impl<T> ui::Panel<T> { fn b(&self) {} }\n"
        "impl ui::Frame { fn c(&self) {} }\n"
        "impl<T> Trait for Vec<crate::Sample> { fn d(&self) {} }\n"
        "impl Trait for &Widget { fn e(&self) {} }\n"
        "impl union<i32> { fn f(&self) {} }\n"
        "impl From<W> for default<u8> { fn g(&self) {} }\n"
    )
    found = [(fn.namespace, fn.name) for fn in functions_in_source(source, path, str(tmp_path))]
    assert found == [
        ("demo::Widget", "a"),
        ("demo::Panel", "b"),
        ("demo::Frame", "c"),
        ("demo::Vec", "d"),
        ("demo", "e"),
        ("demo", "f"),
        ("demo", "g"),
    ]


def test_mod_rs_is_named_after_its_directory(tmp_path):
    _crate(tmp_path)
    found = functions_in_source("pub fn open() {}\n", str(tmp_path / "src" / "game" / "mod.rs"), str(tmp_path))
    assert found[0].namespace == "demo::game"
    nested = functions_in_source("pub fn open() {}\n", str(tmp_path / "src" / "game" / "board" / "mod.rs"), None)
    assert nested[0].namespace == "demo::game::board"


def test_bin_needs_a_file_below_it_to_name_a_binary(tmp_path):
    _crate(tmp_path)
    source = "pub fn open() {}\n"
    layouts = {
        "src/bin.rs": "demo::bin",
        "src/bin/mod.rs": "demo::bin",
        "src/bin/tool/mod.rs": "tool",
        "src/lib/mod.rs": "demo::lib",
        "src/mod.rs": "demo",
        "src/main.rs": "demo",
    }
    found = {
        relative: functions_in_source(source, str(tmp_path / relative), None)[0].namespace
        for relative in layouts
    }
    assert found == layouts
