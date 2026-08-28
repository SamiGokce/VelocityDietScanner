from scripts.occupations import (CATEGORY_PRIORITY, ELIGIBLE_QIDS,
                                 OCCUPATION_CATEGORIES, QID_TO_CATEGORY,
                                 categorise, sparql_values_clause)


def test_every_eligible_occupation_has_a_category():
    for qid in ELIGIBLE_QIDS:
        assert QID_TO_CATEGORY[qid] in OCCUPATION_CATEGORIES


def test_no_occupation_is_listed_under_two_categories():
    seen = set()
    for members in OCCUPATION_CATEGORIES.values():
        for qid in members:
            assert qid not in seen, f"{qid} appears twice"
            seen.add(qid)


def test_qids_are_well_formed():
    for qid in ELIGIBLE_QIDS:
        assert qid.startswith("Q") and qid[1:].isdigit()


def test_categories_match_the_spec_vocabulary():
    allowed = {"Actor", "Musician", "Athlete", "Comedian", "TV Personality",
               "Director", "YouTuber/Creator", "Business/Tech", "Other"}
    assert set(OCCUPATION_CATEGORIES) <= allowed
    assert set(CATEGORY_PRIORITY) == allowed


def test_categorise_picks_the_dominant_occupation():
    assert categorise(["Q33999", "Q10800557", "Q177220"]) == "Actor"
    assert categorise(["Q177220", "Q488205", "Q33999"]) == "Musician"
    assert categorise(["Q17125263"]) == "YouTuber/Creator"
    assert categorise(["Q131524", "Q484876"]) == "Business/Tech"


def test_categorise_breaks_ties_by_priority():
    # one actor occupation, one musician occupation -> Musician wins the tie
    assert categorise(["Q33999", "Q177220"]) == "Musician"
    # athlete outranks everything
    assert categorise(["Q937857", "Q33999"]) == "Athlete"


def test_unknown_or_empty_occupations_fall_back_to_other():
    assert categorise([]) == "Other"
    assert categorise(["Q99999999"]) == "Other"


def test_sparql_values_clause_lists_every_qid():
    clause = sparql_values_clause()
    assert clause.count("wd:") == len(ELIGIBLE_QIDS)
    assert "wd:Q33999" in clause
