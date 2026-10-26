from crapper.languages.rust import functions_in_source

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
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo-game"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
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
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    source = "impl Trait for Vec<crate::Sample> {\n    fn draw(&self) {}\n}\n"
    functions = functions_in_source(source, str(tmp_path / "src" / "lib.rs"), str(tmp_path))
    assert functions[0].name == "draw"
    assert functions[0].namespace.endswith("Vec") or "Sample" in functions[0].namespace


def test_module_paths_outside_the_usual_layout(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
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
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    path = tmp_path / "src" / "game" / "board.rs"
    path.parent.mkdir(parents=True)
    source = "pub fn place() -> i32 { if ready { 1 } else { 0 } }\n"
    path.write_text(source, encoding="utf-8")
    functions = functions_in_source(source, "src/game/board.rs", str(tmp_path))
    assert [(fn.namespace, fn.name, fn.complexity) for fn in functions] == [
        ("demo::game::board", "place", 2)
    ]


def _demo(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )


def test_cfg_test_items_are_test_code(tmp_path):
    _demo(tmp_path)
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

#[cfg(not(test))]
fn shipped() {}
"""
    functions = functions_in_source(source, str(tmp_path / "src" / "lib.rs"), str(tmp_path))
    assert [fn.name for fn in functions] == ["kept", "real", "shipped"]


def test_a_tests_file_module_is_test_code(tmp_path):
    _demo(tmp_path)
    path = tmp_path / "src" / "compose" / "tests.rs"
    assert functions_in_source("fn case() {}\n", str(path), str(tmp_path)) == []
    inner = "#![cfg(test)]\nfn case() {}\n"
    assert functions_in_source(inner, str(tmp_path / "src" / "support.rs"), str(tmp_path)) == []
