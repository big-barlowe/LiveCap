from types import SimpleNamespace

from app.sentences import Joiner, continue_sentence, ends_sentence, group_sentences, guess_language, is_unfinished, join, tidy


def words(text):
    return [{"word": word} for word in text.split()]


def grouped(text):
    return [" ".join(w["word"] for w in sentence) for sentence in group_sentences(words(text))]


def sentence(text, language="en", seconds=1.0):
    return SimpleNamespace(text=text, language=language, seconds=seconds)


def test_abbreviations_and_initials_do_not_end_sentences():
    assert grouped("Dr. Smith met J. Doe today. Next one") == ["Dr. Smith met J. Doe today.", "Next one"]


def test_decimals_and_domains_stay_whole():
    assert grouped("It costs 3.50 at example.com today.") == ["It costs 3.50 at example.com today."]


def test_closing_quote_ends_sentence():
    assert ends_sentence('along."') and not ends_sentence("along,")


def test_tidy_capitalises_and_removes_stray_quote():
    assert tidy('as we go along."') == "As we go along."
    assert tidy('she said "go along."') == 'She said "go along."'


def test_unfinished_endings_by_language():
    assert is_unfinished("We hoped to stay, but.", "en")
    assert is_unfinished("Esperábamos quedarnos, pero.", "es")
    assert is_unfinished("Wir wollten bleiben, aber.", "de")
    assert not is_unfinished("We hoped to stay.", "en")
    assert not is_unfinished("Anything at all.", "xx")


def test_join_removes_stop_and_lowers_only_known_words():
    assert join("What I wanted to say is that.", "The budget changed.", "en") == "What I wanted to say is that the budget changed."
    assert join("We went to.", "London today.", "en") == "We went to London today."


def test_joiner_holds_then_merges_across_several_pauses():
    joiner = Joiner()
    assert joiner.add([sentence("We hoped, but.")]) == []
    assert joiner.held is not None
    out = joiner.add([sentence("That is not possible.", seconds=2.0), sentence("Thanks.")])
    assert [s.text for s in out] == ["We hoped, but that is not possible.", "Thanks."]
    assert out[0].seconds == 3.0
    assert joiner.held is None


def test_joiner_flush_releases_a_sentence_that_never_continued():
    joiner = Joiner()
    joiner.add([sentence("The other thing is.")])
    assert [s.text for s in joiner.flush()] == ["The other thing is."]
    assert joiner.flush() == []


def test_guess_language_from_common_words():
    assert guess_language("We hoped that the budget was for this year") == "en"
    assert guess_language("Esperábamos que el presupuesto de este año") == "es"
    assert guess_language("Wir hoffen dass die Pläne für das Jahr") == "de"
    assert guess_language("zzz", default="fr") == "fr"


def test_continuing_line_loses_its_capital_except_where_it_belongs():
    assert continue_sentence("Point out how it works.", "en") == "point out how it works."
    assert continue_sentence("I went home.", "en") == "I went home."
    assert continue_sentence("NASA said so.", "en") == "NASA said so."
    assert continue_sentence("Hunde bellen.", "de") == "Hunde bellen."
    assert continue_sentence("Die Hunde bellen.", "de") == "die Hunde bellen."
