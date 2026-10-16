from oni_rcon import fuzzy


def test_typos_still_match():
    assert fuzzy.matches("gluch", "Huge Gulch V90")
    assert fuzzy.matches("valhala", "Valhalla")
    assert fuzzy.matches("sandtrp", "Sandtrap")
    assert fuzzy.matches("constuct", "Construct")
    assert fuzzy.matches("highground", "High Ground")
    assert fuzzy.matches("valhal", "Valhalla")          # still typing


def test_unrelated_and_short_terms_do_not_match():
    assert not fuzzy.matches("gluch", "Guardian")
    assert not fuzzy.matches("pit", "Pot")             # short terms must be exact
    assert fuzzy.matches("pit", "The Pit")
    assert not fuzzy.matches("gulch zzzz", "Huge Gulch")  # every term must match


def test_rank_puts_exact_first():
    items = ["Gluch Arena", "Huge Gulch", "Guardian"]
    assert fuzzy.rank("gulch", items, lambda x: (x,)) == ["Huge Gulch", "Gluch Arena"]
    assert fuzzy.rank("", items, lambda x: (x,)) == items
