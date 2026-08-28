"""Wikidata occupation (P106) QIDs -> the nine output categories.

Every QID below was checked against the live Wikidata entity API; the comment
after each one is its English label at the time of writing.  This table is the
single source of truth for two things:

  * eligibility -- the SPARQL candidate query only matches people holding one
    of these occupations, and
  * the `category` column -- so a person can never be selected under an
    occupation the pipeline cannot categorise.

Category names are exactly the ones the spec asks for:
Actor, Musician, Athlete, Comedian, TV Personality, Director,
YouTuber/Creator, Business/Tech, Other.
"""

from __future__ import annotations

ACTOR = "Actor"
MUSICIAN = "Musician"
ATHLETE = "Athlete"
COMEDIAN = "Comedian"
TV_PERSONALITY = "TV Personality"
DIRECTOR = "Director"
CREATOR = "YouTuber/Creator"
BUSINESS_TECH = "Business/Tech"
OTHER = "Other"

OCCUPATION_CATEGORIES: dict[str, dict[str, str]] = {
    ACTOR: {
        "Q33999": "actor",
        "Q10800557": "film actor",
        "Q10798782": "television actor",
        "Q2405480": "voice actor",
        "Q2259451": "stage actor",
        "Q948329": "character actor",
    },
    MUSICIAN: {
        "Q639669": "musician",
        "Q177220": "singer",
        "Q488205": "singer-songwriter",
        "Q753110": "songwriter",
        "Q36834": "composer",
        "Q183945": "record producer",
        "Q855091": "guitarist",
        "Q386854": "drummer",
        "Q584301": "bassist",
        "Q486748": "pianist",
        "Q2252262": "rapper",
        "Q158852": "conductor",
        "Q130857": "disc jockey",
    },
    ATHLETE: {
        "Q2066131": "athlete",
        "Q937857": "association football player",
        "Q3665646": "basketball player",
        "Q10871364": "baseball player",
        "Q19204627": "American football player",
        "Q11774891": "ice hockey player",
        "Q10833314": "tennis player",
        "Q11338576": "boxer",
        "Q13474373": "professional wrestler",
        "Q12299841": "cricketer",
        "Q11303721": "golfer",
        "Q10843402": "swimmer",
        "Q13219587": "figure skater",
        "Q13382576": "rower",
        "Q2309784": "sport cyclist",
        "Q13415036": "rugby player",
        "Q15117302": "volleyball player",
        "Q13141064": "badminton player",
        "Q13365117": "handball player",
        "Q13381863": "fencer",
        "Q11513337": "athletics competitor",
        "Q4009406": "sprinter",
        "Q378622": "racing driver",
        "Q10349745": "racing automobile driver",
    },
    COMEDIAN: {
        "Q245068": "comedian",
        "Q18545066": "stand-up comedian",
    },
    TV_PERSONALITY: {
        "Q947873": "television presenter",
        "Q13590141": "presenter",
        "Q2722764": "radio personality",
    },
    DIRECTOR: {
        "Q2526255": "film director",
        "Q3455803": "director",
        "Q2059704": "television director",
        "Q3387717": "theatre director",
        "Q3282637": "film producer",
        "Q578109": "television producer",
        "Q1053574": "executive producer",
    },
    CREATOR: {
        "Q17125263": "YouTuber",
        "Q2045208": "Internet celebrity",
        "Q2906862": "influencer",
        "Q57414145": "online streamer",
        "Q50279140": "Twitch streamer",
    },
    BUSINESS_TECH: {
        "Q131524": "entrepreneur",
        "Q43845": "businessperson",
        "Q484876": "chief executive officer",
        "Q5482740": "programmer",
        "Q82594": "computer scientist",
        "Q81096": "engineer",
        "Q205375": "inventor",
    },
}

# Flat QID -> category lookup.
QID_TO_CATEGORY: dict[str, str] = {
    qid: category
    for category, members in OCCUPATION_CATEGORIES.items()
    for qid in members
}

ELIGIBLE_QIDS: tuple[str, ...] = tuple(QID_TO_CATEGORY)

# Tie-break order when someone holds occupations in several categories and no
# single category dominates by count.  Roughly "what is this person best known
# for" -- a singer-and-actor is filed under Musician, an athlete-turned-
# presenter under Athlete.
CATEGORY_PRIORITY: tuple[str, ...] = (
    ATHLETE, MUSICIAN, ACTOR, COMEDIAN, CREATOR, DIRECTOR,
    TV_PERSONALITY, BUSINESS_TECH, OTHER,
)


def categorise(occupation_qids: list[str] | tuple[str, ...]) -> str:
    """Map a person's P106 values to one output category.

    The category with the most matching occupations wins; ties are broken by
    CATEGORY_PRIORITY.  Unknown occupations are ignored, and a person with no
    recognised occupation at all falls back to "Other".
    """
    counts: dict[str, int] = {}
    for qid in occupation_qids:
        category = QID_TO_CATEGORY.get(qid)
        if category:
            counts[category] = counts.get(category, 0) + 1
    if not counts:
        return OTHER
    best = max(counts.values())
    winners = [c for c, n in counts.items() if n == best]
    for category in CATEGORY_PRIORITY:
        if category in winners:
            return category
    return OTHER


def sparql_values_clause() -> str:
    """`wd:Q33999 wd:Q10800557 ...` for a SPARQL VALUES block."""
    return " ".join(f"wd:{qid}" for qid in ELIGIBLE_QIDS)
