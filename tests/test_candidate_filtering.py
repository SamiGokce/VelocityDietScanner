"""Turning detail-query rows into candidates -- and dropping the ones we must."""

from datetime import date

from scripts.wikidata import WikidataClient, _article_title, _image_filename, _parse_dob

DAY = date(2026, 8, 28)


def row(qid, label, dob, sitelinks, occupations, image=None, article=None, dead=None):
    binding = {
        "person": {"value": f"http://www.wikidata.org/entity/{qid}"},
        "dob": {"value": dob},
        "sitelinks": {"value": str(sitelinks)},
        "occupations": {"value": ",".join(occupations)},
    }
    if label:
        binding["personLabel"] = {"value": label}
    if image:
        binding["image"] = {"value": image}
    if article:
        binding["article"] = {"value": article}
    if dead:
        binding["dead"] = {"value": dead}
    return binding


def candidates(rows, curated=()):
    return WikidataClient._to_candidates(rows, DAY, set(curated))


def test_a_usable_person_survives():
    out = candidates([row(
        "Q1", "Alive Actor", "1969-08-28T00:00:00Z", 120, ["Q33999"],
        image="http://commons.wikimedia.org/wiki/Special:FilePath/Foo%20bar.jpg",
        article="https://en.wikipedia.org/wiki/Alive_Actor",
    )])
    assert len(out) == 1
    person = out[0]
    assert person.full_name == "Alive Actor"
    assert person.category == "Actor"
    assert person.image_filename == "Foo bar.jpg"
    assert person.wikipedia_title == "Alive Actor"
    assert person.birth_date == date(1969, 8, 28)


def test_a_date_of_death_is_dropped_even_if_it_reached_this_far():
    """Query A excludes them, but a stale cache or curated entry could not."""
    assert candidates([row("Q5", "Dead Person", "1950-08-28T00:00:00Z", 300,
                           ["Q33999"], dead="2020-01-01T00:00:00Z")]) == []


def test_a_different_day_is_dropped():
    assert candidates([row("Q2", "Wrong Day", "1969-08-27T00:00:00Z", 120, ["Q33999"])]) == []


def test_an_ineligible_occupation_is_dropped():
    assert candidates([row("Q3", "Physicist", "1969-08-28T00:00:00Z", 400, ["Q901"])]) == []


def test_a_person_with_no_english_label_is_dropped():
    """There would be nothing to put on the graphic."""
    assert candidates([row("Q4", None, "1969-08-28T00:00:00Z", 120, ["Q33999"])]) == []
    assert candidates([row("Q4", "Q4", "1969-08-28T00:00:00Z", 120, ["Q33999"])]) == []


def test_curated_people_are_marked_and_keep_their_low_sitelink_count():
    out = candidates(
        [row("Q6", "Curated Creator", "2000-08-28T00:00:00Z", 5, ["Q17125263"])],
        curated=["Q6"],
    )
    assert out[0].curated is True
    assert out[0].sitelinks == 5
    assert out[0].category == "YouTuber/Creator"


def test_a_missing_photo_is_not_fatal_here():
    """The photo is licence-checked later; absence is handled by the fetcher."""
    out = candidates([row("Q7", "No Photo", "1969-08-28T00:00:00Z", 90, ["Q177220"])])
    assert out[0].image_filename is None


def test_uri_parsing_helpers():
    assert _image_filename(
        "http://commons.wikimedia.org/wiki/Special:FilePath/Jack%20Black%202017.jpg"
    ) == "Jack Black 2017.jpg"
    assert _image_filename(None) is None
    assert _article_title("https://en.wikipedia.org/wiki/Robert_De_Niro") == "Robert De Niro"
    assert _parse_dob("1969-08-28T00:00:00Z") == date(1969, 8, 28)
    assert _parse_dob("not a date") is None
    assert _parse_dob("0000-00-00T00:00:00Z") is None
