from app.translate import BpeTokens


def tokens(tmp_path, codes):
    path = tmp_path / "bpe.model"
    path.write_text("#version: 0.2\n" + codes + "\n", encoding="utf-8")
    return BpeTokens(path, "es", "en")


def test_merges_in_rank_order_and_marks_word_pieces(tmp_path):
    bpe = tokens(tmp_path, "h o\nl a</w>\nho l")
    assert bpe.encode("hola") == ["ho@@", "la"]


def test_a_whole_word_stays_unmarked(tmp_path):
    bpe = tokens(tmp_path, "s i</w>")
    assert bpe.encode("si") == ["si"]


def test_punctuation_is_split_from_words_and_escaped_as_the_models_were_trained(tmp_path):
    bpe = tokens(tmp_path, "s i</w>")
    assert bpe.encode("¿si?") == ["¿", "si", "?"]
    assert "&apos;" in "".join(bpe.encode("l'a")).replace("@@", "")


def test_decode_joins_pieces_and_attaches_punctuation(tmp_path):
    bpe = tokens(tmp_path, "s i</w>")
    assert bpe.decode(["Hel@@", "lo", ",", "wor@@", "ld", "!"]) == "Hello, world!"


def test_decode_restores_escaped_characters(tmp_path):
    bpe = tokens(tmp_path, "s i</w>")
    assert bpe.decode(["Where", "&apos;s", "Mr.", "Garcia", "?"]) == "Where's Mr. Garcia?"
    assert bpe.decode(["I", "don", "&apos;t", "know", "."]) == "I don't know."


def test_hyphens_are_split_as_the_models_were_trained_and_restored(tmp_path):
    bpe = tokens(tmp_path, "s i</w>\n@ -\n@- @</w>")
    assert bpe.encode("si-si") == ["si", "@-@", "si"]
    assert bpe.decode(["end", "@-@", "e", "?"]) == "end-e?"


def test_decode_keeps_times_together(tmp_path):
    bpe = tokens(tmp_path, "s i</w>")
    assert bpe.decode(["at", "5", ":", "00", "."]) == "at 5:00."


def test_decode_copes_with_output_ending_on_a_word_piece(tmp_path):
    bpe = tokens(tmp_path, "s i</w>")
    assert bpe.decode(["Hel@@"]) == "Hel"


def test_codes_may_hold_characters_that_look_like_line_breaks(tmp_path):
    bpe = tokens(tmp_path, "a  b</w>\ns i</w>")
    assert bpe.ranks == {("a ", "b</w>"): 0, ("s", "i</w>"): 1}
