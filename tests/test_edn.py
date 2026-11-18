"""The EDN reader the snapshot test trusts has to reject what is not EDN."""

import pytest

from edn import EdnError, Keyword, read_edn


def test_reads_the_shape_crapper_writes():
    text = (
        "{:entries [\n"
        '  {:name "choose", :namespace "demo.core", :complexity 2, :coverage nil, :crap nil}\n'
        '  {:name "place", :namespace "demo.Board", :complexity 3, :coverage 83.3333, :crap 7.5}\n'
        "]}\n"
    )
    assert read_edn(text) == {
        Keyword("entries"): [
            {Keyword("name"): "choose", Keyword("namespace"): "demo.core",
             Keyword("complexity"): 2, Keyword("coverage"): None, Keyword("crap"): None},
            {Keyword("name"): "place", Keyword("namespace"): "demo.Board",
             Keyword("complexity"): 3, Keyword("coverage"): 83.3333, Keyword("crap"): 7.5},
        ]
    }


def test_escapes_are_read_as_the_character_they_name():
    assert read_edn('{:entries [] :name "a\\"b\\\\c\\n"}') == {
        Keyword("entries"): [], Keyword("name"): 'a"b\\c\n',
    }


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{:entries []",
        "{:entries [}",
        "{:entries []} trailing",
        '{"entries" []}',
        "{:a 1, :a 2}",
        '{:name "unterminated}',
        '{:name "bad \\q escape"}',
        "{:name foo}",
        "[1 2",
    ],
)
def test_rejects_anything_else(text):
    with pytest.raises(EdnError):
        read_edn(text)
