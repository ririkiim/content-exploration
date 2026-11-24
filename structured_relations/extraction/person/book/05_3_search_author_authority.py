"""
Final automated book author authority search.

Input:
    05_2_unmatched_authors.jsonl

Methods:
    1. Normalized title search
    2. Direct authority name search
    3. Authority metadata retrieval
    4. Book-author cross validation
    5. Existing book author identifier matching

No database writes.
"""

import argparse
import csv
import importlib
import json
import os
import re
import unicodedata

from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from structured_relations.extraction.person.book.title_normalization import (
    title_variants,
    normalize_title,
    compare_titles,
    STRONG_TITLE_MATCHES,
    MATCH_SCORES,
)

m = importlib.import_module(
    "01_collect_author_authority"
)

db = importlib.import_module(
    "05_2_match_existing_authors"
)


# =========================================================
# 1. Configuration
# =========================================================

VERSION = "book-author-authority-search-v1"

OUT = m.OUT
BASE = m.BASE

NAME_PREDICATES = """
    rdfs:label
    foaf:name
    skos:prefLabel
    skos:altLabel
"""

KAC_RE = re.compile(
    r"KAC[0-9A-Z]+"
)

PAPER_BOOK = (
    "http://lod.nl.go.kr/ontology/Book"
)

OFFLINE_MATERIAL = (
    "http://lod.nl.go.kr/ontology/OfflineMaterial"
)

ONLINE_MATERIAL = (
    "http://lod.nl.go.kr/ontology/OnlineMaterial"
)

# Verified search spellings from the probe.
# These are retrieval hints, not identity decisions.

SEARCH_NAME_HINTS = {
    "헤르만헤세": [
        "헤세, 헤르만",
        "Hesse, Hermann",
    ],
    "jk롤링": [
        "롤링, J. K.",
        "Rowling, J. K.",
    ],
}


# =========================================================
# 2. Basic utilities
# =========================================================

def clean(value):

    return re.sub(
        r"\s+",
        " ",
        str(value or ""),
    ).strip()


def name_key(value):

    value = unicodedata.normalize(
        "NFKC",
        clean(value),
    ).casefold()

    return re.sub(
        r"[\s.,·ㆍ・･．]",
        "",
        value,
    )


def kac_from_uri(uri):

    uri = str(uri or "")

    if not uri.startswith(BASE):
        return None

    value = uri[len(BASE):]

    if KAC_RE.fullmatch(value):
        return value

    return None


def read_jsonl(path):

    with path.open(
        encoding="utf-8-sig"
    ) as f:

        return [
            json.loads(line)
            for line in f
            if line.strip()
        ]


def write_jsonl(path, rows):

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    with tmp.open(
        "w",
        encoding="utf-8",
    ) as f:

        for row in rows:

            f.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    default=str,
                ) + "\n"
            )

    tmp.replace(path)


def write_json(path, value):

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    tmp.replace(path)


# =========================================================
# 3. Search name variants
# =========================================================

def normalize_initials(value):

    value = re.sub(
        r"\b([A-Za-z])\s*\.?\s*(?=[A-Za-z]\.)",
        r"\1. ",
        value,
    )

    value = re.sub(
        r"\b([A-Za-z])\.(?=\S)",
        r"\1. ",
        value,
    )

    return clean(value)


def name_variants(name):

    original = clean(name)

    if not original:
        return []

    result = []

    def add(value):

        value = clean(value)

        if value and value not in result:
            result.append(value)

    add(original)

    normalized = unicodedata.normalize(
        "NFKC",
        original,
    )

    add(normalized)

    initial_form = normalize_initials(
        normalized
    )

    add(initial_form)

    # J.K 롤링 -> J. K. 롤링

    match = re.fullmatch(
        r"([A-Za-z.\s]+)\s+([가-힣]+)",
        normalized,
    )

    if match:

        initial_text, surname = match.groups()

        letters = re.sub(
            r"[^A-Za-z]",
            "",
            initial_text,
        )

        if 1 <= len(letters) <= 4:

            initials = " ".join(
                letter.upper() + "."
                for letter in letters
            )

            add(
                f"{initials} {surname}"
            )

            add(
                f"{surname}, {initials}"
            )

    # Convert comma-separated names back to
    # given-name surname order.

    if "," in normalized:

        surname, given = normalized.split(
            ",",
            1,
        )

        add(
            f"{given.strip()} {surname.strip()}"
        )

    # 헤르만 헤세 -> 헤세, 헤르만
    # 요한 볼프강 괴테 -> 괴테, 요한 볼프강
    #
    # These are retrieval variants only.

    for value in list(result):

        parts = value.split()

        if (
            len(parts) >= 2
            and "," not in value
        ):

            surname = parts[-1]

            given = " ".join(
                parts[:-1]
            )

            add(
                f"{surname}, {given}"
            )

    for hint in SEARCH_NAME_HINTS.get(
        name_key(original),
        [],
    ):

        add(hint)

    return result


def names_match(input_name, authority_names):

    # Compare normalized keys of generated
    # search variants on both sides.

    left = {
        name_key(value)
        for value in name_variants(
            input_name
        )
    }

    right = {
        name_key(value)
        for name in authority_names
        for value in name_variants(name)
    }

    return bool(
        left & right
    )


# =========================================================
# 4. Persistent query cache
# =========================================================

class QueryCache:

    def __init__(self, path, client):

        self.path = path
        self.client = client
        self.data = {}

        self.hits = 0
        self.calls = 0
        self.errors = 0

        if path.exists():

            for row in read_jsonl(path):

                if row.get("version") != VERSION:
                    continue

                if row.get("status") != "ok":
                    continue

                self.data[
                    row["cache_key"]
                ] = row["rows"]

    def query(self, kind, key, query):

        cache_key = json.dumps(
            [kind, key],
            ensure_ascii=False,
            sort_keys=True,
        )

        if cache_key in self.data:

            self.hits += 1

            return (
                self.data[cache_key],
                True,
            )

        self.calls += 1

        try:

            rows = self.client.query(query)

        except RuntimeError as e:

            self.errors += 1

            record = {
                "version": VERSION,
                "cache_key": cache_key,
                "status": "error",
                "error": str(e),
            }

            self.append(record)

            print(
                f"[QUERY ERROR] {kind}: {key}"
            )

            return [], False

        record = {
            "version": VERSION,
            "cache_key": cache_key,
            "status": "ok",
            "rows": rows,
        }

        self.append(record)

        self.data[cache_key] = rows

        return rows, True

    def append(self, row):

        with self.path.open(
            "a",
            encoding="utf-8",
        ) as f:

            f.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                ) + "\n"
            )


# =========================================================
# 5. SPARQL queries
# =========================================================

def search_name_query(name):

    literal = m.literals([name])

    # DISTINCT ?author prevents duplicate
    # KAC results across multiple predicates.

    return f"""
    SELECT DISTINCT ?author
    WHERE {{
        VALUES ?name {{ {literal} }}

        VALUES ?predicate {{
            {NAME_PREDICATES}
        }}

        ?author ?predicate ?name .

        FILTER(
            STRSTARTS(
                STR(?author),
                "{BASE}KAC"
            )
        )
    }}
    """


def search_title_query(title):

    literal = m.literals([title])

    return f"""
    SELECT DISTINCT ?book
    WHERE {{
        VALUES ?title {{ {literal} }}

        VALUES ?predicate {{
            dcterms:title
            dcterms:alternative
        }}

        ?book ?predicate ?title .

        ?book a nlon:Book .
        ?book a nlon:OfflineMaterial .

        FILTER NOT EXISTS {{
            ?book a nlon:OnlineMaterial .
        }}
    }}
    """


def book_detail_query(book):

    return f"""
    SELECT DISTINCT ?predicate ?value
    WHERE {{
        VALUES ?predicate {{
            dcterms:title
            dcterms:alternative
            rdfs:label
            dcterms:creator
            bibo:isbn
            rdf:type
        }}

        <{book}> ?predicate ?value .
    }}
    """


def inverse_creator_query(book):

    return f"""
    SELECT DISTINCT ?author
    WHERE {{
        ?author nlon:create <{book}> .

        FILTER(
            STRSTARTS(
                STR(?author),
                "{BASE}KAC"
            )
        )
    }}
    """


def authority_query(author):

    return f"""
    SELECT DISTINCT ?predicate ?value
    WHERE {{
        VALUES ?predicate {{
            {NAME_PREDICATES}

            nlon:isni
            nlon:birthYear
            nlon:fieldOfActivity

            <http://purl.org/dc/elements/1.1/source>

            <http://schema.org/jobTitle>

            <http://www.w3.org/2002/07/owl#sameAs>

            rdf:type
        }}

        <{author}> ?predicate ?value .
    }}
    """


def author_title_query(author, title):

    literal = m.literals([title])

    # Do not retrieve all works of the author.
    # Search only works related to the target title.

    return f"""
    SELECT DISTINCT ?book ?matched_title
    WHERE {{
        VALUES ?matched_title {{ {literal} }}

        VALUES ?predicate {{
            dcterms:title
            dcterms:alternative
        }}

        {{
            <{author}> nlon:create ?book .
        }}
        UNION
        {{
            ?book dcterms:creator <{author}> .
        }}

        ?book ?predicate ?matched_title .

        ?book a nlon:Book .
        ?book a nlon:OfflineMaterial .

        FILTER NOT EXISTS {{
            ?book a nlon:OnlineMaterial .
        }}
    }}
    """


# =========================================================
# 6. Parse authority records
# =========================================================

def load_authority(cache, author):

    rows, complete = cache.query(
        "authority",
        author,
        authority_query(author),
    )

    names = set()
    preferred_names = set()

    isni = set()
    invalid_isni = set()

    birth_years = set()
    fields = set()
    jobs = set()
    sources = set()
    same_as = set()
    types = set()

    for row in rows:

        predicate = row.get(
            "predicate",
            "",
        )

        value = clean(
            row.get("value")
        )

        if not value:
            continue

        if predicate.endswith((
            "#label",
            "#prefLabel",
            "#altLabel",
            "/name",
        )):

            names.add(value)

            if not predicate.endswith(
                "#altLabel"
            ):

                preferred_names.add(value)

        elif predicate.endswith("/isni"):

            valid = db.valid_isni(value)

            if valid:
                isni.add(valid)

            else:
                invalid_isni.add(value)

        elif predicate.endswith(
            "/birthYear"
        ):

            birth_years.add(value)

        elif predicate.endswith(
            "/fieldOfActivity"
        ):

            fields.add(value)

        elif predicate.endswith(
            "/jobTitle"
        ):

            jobs.add(value)

        elif predicate.endswith(
            "/source"
        ):

            sources.add(value)

        elif predicate.endswith(
            "#sameAs"
        ):

            same_as.add(value)

            match = re.search(
                r"isni\.org/isni/"
                r"([0-9Xx\s-]+)$",
                value,
            )

            if match:

                valid = db.valid_isni(
                    match.group(1)
                )

                if valid:
                    isni.add(valid)

        elif predicate.endswith(
            "#type"
        ):

            types.add(value)

    return {
        "authority_uri": author,

        "kac": kac_from_uri(author),

        "names": sorted(names),

        "preferred_names": sorted(
            preferred_names
        ),

        "isni": sorted(isni),

        "invalid_isni": sorted(
            invalid_isni
        ),

        "birth_years": sorted(
            birth_years
        ),

        "fields_of_activity": sorted(
            fields
        ),

        "jobs": sorted(jobs),

        "sources": sorted(sources),

        "same_as": sorted(same_as),

        "rdf_types": sorted(types),

        "authority_complete": complete,

        "identifier_conflict": bool(
            len(isni) > 1
            or invalid_isni
        ),

        "raw_authority": rows,
    }


# =========================================================
# 7. Parse bibliographic records
# =========================================================

def load_book(cache, book):

    rows, detail_complete = cache.query(
        "book_detail",
        book,
        book_detail_query(book),
    )

    titles = set()
    authors = set()
    isbn = set()
    types = set()

    for row in rows:

        predicate = row.get(
            "predicate",
            "",
        )

        value = clean(
            row.get("value")
        )

        if not value:
            continue

        if predicate.endswith((
            "/title",
            "/alternative",
            "#label",
        )):

            titles.add(value)

        elif predicate.endswith(
            "/creator"
        ):

            if kac_from_uri(value):
                authors.add(value)

        elif predicate.endswith(
            "/isbn"
        ):

            isbn.add(value)

        elif predicate.endswith(
            "#type"
        ):

            types.add(value)

    inverse_rows, inverse_complete = (
        cache.query(
            "inverse_creator",
            book,
            inverse_creator_query(book),
        )
    )

    for row in inverse_rows:

        author = row.get("author")

        if kac_from_uri(author):
            authors.add(author)

    paper = (
        PAPER_BOOK in types
        and OFFLINE_MATERIAL in types
        and ONLINE_MATERIAL not in types
    )

    return {
        "book_uri": book,

        "titles": sorted(titles),

        "authors": sorted(authors),

        "isbn": sorted(isbn),

        "rdf_types": sorted(types),

        "paper_book": paper,

        "complete": (
            detail_complete
            and inverse_complete
        ),

        "raw_book": rows,
    }


# =========================================================
# 8. Existing DB identifier index
# =========================================================

def load_existing_identifiers():

    url = os.getenv(
        "DATABASE_URL"
    )

    if not url:

        raise RuntimeError(
            "DATABASE_URL missing"
        )

    with psycopg.connect(
        url,
        row_factory=dict_row,
    ) as conn:

        conn.execute(
            "SET TRANSACTION READ ONLY"
        )

        people, _ = db.load_index(conn)

        conn.rollback()

    kac_index = defaultdict(set)
    isni_index = defaultdict(set)

    for eid, person in people.items():

        for kac in person["kac_ids"]:

            kac_index[kac].add(eid)

        for isni in person["isni"]:

            isni_index[isni].add(eid)

    return (
        people,
        kac_index,
        isni_index,
    )


def match_existing_identifiers(
    candidate,
    people,
    kac_index,
    isni_index,
):

    matches = set()

    kac = candidate.get("kac")

    if kac:

        matches.update(
            kac_index.get(kac, set())
        )

    for isni in candidate.get(
        "isni",
        [],
    ):

        matches.update(
            isni_index.get(isni, set())
        )

    conflict = (
        len(matches) > 1
    )

    for eid in matches:

        person = people[eid]

        if person.get(
            "identifier_conflict"
        ):

            conflict = True

        existing_isni = set(
            person["isni"]
        )

        incoming_isni = set(
            candidate.get("isni", [])
        )

        if (
            existing_isni
            and incoming_isni
            and existing_isni != incoming_isni
        ):

            conflict = True

    return {
        "existing_entity_ids": sorted(
            matches
        ),

        "existing_identifier_conflict": (
            conflict
        ),
    }


# =========================================================
# 9. Direct name search
# =========================================================

def search_author_names(cache, names):

    authors = defaultdict(set)

    complete = True

    queries = sorted({
        variant
        for name in names
        for variant in name_variants(name)
    })

    for query_name in queries:

        rows, ok = cache.query(
            "author_name",
            query_name,
            search_name_query(query_name),
        )

        if not ok:
            complete = False

        for row in rows:

            author = row.get("author")

            if kac_from_uri(author):

                authors[author].add(
                    query_name
                )

    return (
        authors,
        queries,
        complete,
    )


# =========================================================
# 10. Title-based candidate search
# =========================================================

def search_books_by_title(cache, title):

    books = {}

    complete = True

    queries = title_variants(title)

    for query_title in queries:

        rows, ok = cache.query(
            "title_search",
            query_title,
            search_title_query(query_title),
        )

        if not ok:
            complete = False

        for row in rows:

            book_uri = row.get("book")

            if not book_uri:
                continue

            if not book_uri.startswith(BASE):
                continue

            if book_uri not in books:

                books[book_uri] = load_book(
                    cache,
                    book_uri,
                )

            if not books[book_uri]["complete"]:

                complete = False

    return (
        books,
        queries,
        complete,
    )


# =========================================================
# 11. Authority-to-title cross validation
# =========================================================

def search_author_works(
    cache,
    author,
    queries,
):

    books = {}

    complete = True

    for title in queries:

        rows, ok = cache.query(
            "author_title",
            [author, title],
            author_title_query(
                author,
                title,
            ),
        )

        if not ok:
            complete = False

        for row in rows:

            book_uri = row.get("book")

            if not book_uri:
                continue

            if not book_uri.startswith(BASE):
                continue

            if book_uri not in books:

                books[book_uri] = load_book(
                    cache,
                    book_uri,
                )

            if not books[book_uri]["complete"]:

                complete = False

    return books, complete


# =========================================================
# 12. Compare requested title with bibliographic title
# =========================================================

def best_title_match(
    requested_title,
    book_titles,
):

    matches = []

    for title in book_titles:

        method = compare_titles(
            requested_title,
            title,
        )

        matches.append({
            "matched_title": title,

            "title_match": method,

            "score": MATCH_SCORES.get(
                method,
                0,
            ),
        })

    matches.sort(
        key=lambda row: (
            -row["score"],
            row["matched_title"],
        )
    )

    if matches:

        return matches[0]

    return {
        "matched_title": None,
        "title_match": "name_only",
        "score": 0,
    }


# =========================================================
# 13. Evaluate individual authority candidate
# =========================================================

def evaluate_authority(
    row,
    authority,
    books,
    search_names,
    people,
    kac_index,
    isni_index,
):

    author_uri = authority[
        "authority_uri"
    ]

    author_name = row[
        "author_name"
    ]

    matched_name = names_match(
        author_name,
        authority["names"],
    )

    evidence = []

    for book in books.values():

        if not book["paper_book"]:
            continue

        if author_uri not in book["authors"]:
            continue

        match = best_title_match(
            row["title"],
            book["titles"],
        )

        if match["title_match"] == "name_only":
            continue

        evidence.append({
            "book_uri": book[
                "book_uri"
            ],

            "book_titles": book[
                "titles"
            ],

            "matched_title": match[
                "matched_title"
            ],

            "title_match": match[
                "title_match"
            ],

            "title_match_score": match[
                "score"
            ],

            "isbn": book["isbn"],

            "paper_book": book[
                "paper_book"
            ],

            "book_lookup_complete": book[
                "complete"
            ],
        })

    evidence.sort(
        key=lambda item: (
            -item["title_match_score"],
            item["book_uri"],
        )
    )

    strong_evidence = [
        item
        for item in evidence
        if item["title_match"]
        in STRONG_TITLE_MATCHES
        and item["book_lookup_complete"]
    ]

    existing = match_existing_identifiers(
        authority,
        people,
        kac_index,
        isni_index,
    )

    # Identifier and name matching alone
    # are insufficient.
    #
    # A strong title match with a paper
    # bibliographic record is also required.

    eligible = bool(
        matched_name
        and strong_evidence
        and authority["authority_complete"]
        and (
            authority["kac"]
            or authority["isni"]
        )
        and not authority["identifier_conflict"]
        and not existing[
            "existing_identifier_conflict"
        ]
    )

    return {
        **authority,
        **existing,

        "matched_search_names": sorted(
            search_names
        ),

        "author_name_match": matched_name,

        "work_evidence": evidence,

        "strong_work_evidence_count": len(
            strong_evidence
        ),

        "best_title_match": (
            evidence[0]["title_match"]
            if evidence
            else "name_only"
        ),

        "eligible": eligible,
    }


# =========================================================
# 14. Classify final candidates
# =========================================================

def classify(candidates, complete):

    # Do not automatically accept a result
    # when some searches have failed.

    if not complete:

        return (
            "lookup_incomplete",
            None,
        )

    if not candidates:

        return (
            "authority_not_found",
            None,
        )

    # Check all strong name-title candidates,
    # including candidates with identifier conflicts.

    strong = [
        candidate
        for candidate in candidates
        if candidate["author_name_match"]
        and candidate[
            "strong_work_evidence_count"
        ] > 0
    ]

    if not strong:

        if any(
            candidate["author_name_match"]
            for candidate in candidates
        ):

            return (
                "authority_found_work_unverified",
                None,
            )

        return (
            "author_name_unverified",
            None,
        )

    if any(
        candidate["identifier_conflict"]
        or candidate[
            "existing_identifier_conflict"
        ]
        for candidate in strong
    ):

        return (
            "identifier_conflict",
            None,
        )

    eligible = [
        candidate
        for candidate in strong
        if candidate["eligible"]
    ]

    if not eligible:

        return (
            "authority_review_required",
            None,
        )

    # Multiple KAC records may refer to
    # one identity if they share the same ISNI.

    identity_groups = defaultdict(list)

    for candidate in eligible:

        if len(candidate["isni"]) == 1:

            identity_key = (
                "ISNI:"
                + candidate["isni"][0]
            )

        else:

            identity_key = (
                "KAC:"
                + candidate["kac"]
            )

        identity_groups[
            identity_key
        ].append(candidate)

    if len(identity_groups) != 1:

        return (
            "multiple_authority_candidates",
            None,
        )

    identity_key, members = next(
        iter(identity_groups.items())
    )

    existing_ids = {
        eid
        for candidate in members
        for eid in candidate[
            "existing_entity_ids"
        ]
    }

    if len(existing_ids) > 1:

        return (
            "identifier_conflict",
            None,
        )

    kac_ids = sorted({
        candidate["kac"]
        for candidate in members
        if candidate["kac"]
    })

    isni = sorted({
        value
        for candidate in members
        for value in candidate["isni"]
    })

    selected = {
        "identity_key": identity_key,

        "kac_ids": kac_ids,

        "isni": isni,

        "authority_uris": sorted({
            candidate["authority_uri"]
            for candidate in members
        }),

        "authority_names": sorted({
            name
            for candidate in members
            for name in candidate["names"]
        }),

        "existing_entity_id": (
            next(iter(existing_ids))
            if existing_ids
            else None
        ),

        "evidence": [
            {
                **evidence,

                "authority_uri": candidate[
                    "authority_uri"
                ],
            }

            for candidate in members
            for evidence in candidate[
                "work_evidence"
            ]

            if evidence["title_match"]
            in STRONG_TITLE_MATCHES

            and evidence[
                "book_lookup_complete"
            ]
        ],
    }

    if existing_ids:

        return (
            "reuse_existing_authority_candidate",
            selected,
        )

    return (
        "new_authority_candidate",
        selected,
    )


# =========================================================
# 15. Process individual book-author row
# =========================================================

def process_row(
    row,
    cache,
    name_authors,
    name_search_complete,
    people,
    kac_index,
    isni_index,
):

    complete = name_search_complete

    books, queries, title_complete = (
        search_books_by_title(
            cache,
            row["title"],
        )
    )

    if not title_complete:
        complete = False

    author_uris = set(
        name_authors
    )

    # Add candidates found from title search.

    for book in books.values():

        author_uris.update(
            book["authors"]
        )

    # Add identifiers from existing
    # 05-2 book-author candidates.
    #
    # These are candidate hints only.

    for candidate in row.get(
        "candidates",
        [],
    ):

        for kac in candidate.get(
            "kac_ids",
            [],
        ):

            if db.valid_kac(kac):

                author_uris.add(
                    BASE + kac
                )

    results = []

    for author_uri in sorted(
        author_uris
    ):

        authority = load_authority(
            cache,
            author_uri,
        )

        if not authority[
            "authority_complete"
        ]:

            complete = False

        # Search author works only when
        # the name is compatible.

        if names_match(
            row["author_name"],
            authority["names"],
        ):

            author_books, ok = (
                search_author_works(
                    cache,
                    author_uri,
                    queries,
                )
            )

            if not ok:
                complete = False

            candidate_books = {
                **books,
                **author_books,
            }

        else:

            candidate_books = books

        result = evaluate_authority(
            row=row,

            authority=authority,

            books=candidate_books,

            search_names=name_authors.get(
                author_uri,
                set(),
            ),

            people=people,

            kac_index=kac_index,

            isni_index=isni_index,
        )

        results.append(result)

    results.sort(
        key=lambda item: (
            not item["eligible"],

            -MATCH_SCORES.get(
                item["best_title_match"],
                0,
            ),

            not item["author_name_match"],

            item["kac"] or "",
        )
    )

    status, selected = classify(
        results,
        complete,
    )

    return {
        **row,

        "authority_search_version": VERSION,

        "title_search_variants": queries,

        "lookup_complete": complete,

        "authority_status": status,

        "authority_candidate_count": len(
            results
        ),

        "selected_authority": selected,

        "authority_candidates": results,
    }


# =========================================================
# 16. Manual review CSV
# =========================================================

def make_review_row(row):

    candidates = row[
        "authority_candidates"
    ]

    return {
        "group_id": row.get(
            "group_id"
        ),

        "book_edition_id": row[
            "book_edition_id"
        ],

        "content_id": row[
            "content_id"
        ],

        "title": row["title"],

        "normalized_title": normalize_title(
            row["title"]
        ),

        "isbn13": row.get(
            "isbn13",
            "",
        ),

        "pub_year": row.get(
            "pub_year",
            "",
        ),

        "author_name": row[
            "author_name"
        ],

        "previous_match_status": row.get(
            "match_status"
        ),

        "authority_status": row[
            "authority_status"
        ],

        "candidate_count": len(
            candidates
        ),

        "candidate_kac": " | ".join(
            candidate["kac"]
            for candidate in candidates
            if candidate["kac"]
        ),

        "candidate_isni": " | ".join(
            f'{candidate["kac"]}: '
            + ",".join(candidate["isni"])

            for candidate in candidates

            if candidate["isni"]
        ),

        "candidate_names": " | ".join(
            f'{candidate["kac"]}: '
            + ", ".join(
                candidate["names"]
            )

            for candidate in candidates
        ),

        "candidate_birth_years": " | ".join(
            f'{candidate["kac"]}: '
            + ", ".join(
                candidate["birth_years"]
            )

            for candidate in candidates

            if candidate["birth_years"]
        ),

        "candidate_fields": " | ".join(
            f'{candidate["kac"]}: '
            + ", ".join(
                candidate[
                    "fields_of_activity"
                ]
            )

            for candidate in candidates

            if candidate[
                "fields_of_activity"
            ]
        ),

        "candidate_entity_ids": " | ".join(
            str(eid)

            for candidate in candidates

            for eid in candidate[
                "existing_entity_ids"
            ]
        ),

        "candidate_work_evidence": " | ".join(
            f'{candidate["kac"]}: '
            f'{evidence["matched_title"]} '
            f'[{evidence["title_match"]}]'

            for candidate in candidates

            for evidence in candidate[
                "work_evidence"
            ]
        ),

        "candidate_authority_urls": " | ".join(
            candidate["authority_uri"]

            for candidate in candidates
        ),

        "verified_author_name": "",

        "verified_kac": "",

        "verified_isni": "",

        "selected_entity_id": "",

        "evidence_isbn": "",

        "evidence_url": "",

        "same_person_confirmed": "pending",

        "review_status": "pending",

        "review_note": "",
    }


def write_review_csv(path, rows):

    fields = [
        "group_id",
        "book_edition_id",
        "content_id",
        "title",
        "normalized_title",
        "isbn13",
        "pub_year",
        "author_name",
        "previous_match_status",
        "authority_status",
        "candidate_count",
        "candidate_kac",
        "candidate_isni",
        "candidate_names",
        "candidate_birth_years",
        "candidate_fields",
        "candidate_entity_ids",
        "candidate_work_evidence",
        "candidate_authority_urls",
        "verified_author_name",
        "verified_kac",
        "verified_isni",
        "selected_entity_id",
        "evidence_isbn",
        "evidence_url",
        "same_person_confirmed",
        "review_status",
        "review_note",
    ]

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    with tmp.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for row in rows:

            writer.writerow(
                make_review_row(row)
            )

    tmp.replace(path)


# =========================================================
# 17. Main
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Test only the first N author-name groups.",
    )

    parser.add_argument(
        "--author",
        action="append",
        default=[],
        help="Test selected author names.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:

        parser.error(
            "--limit must be positive"
        )

    if args.delay < 0:

        parser.error(
            "--delay must be non-negative"
        )

    input_path = (
        OUT
        / "05_2_unmatched_authors.jsonl"
    )

    output_paths = {
        "results": (
            OUT
            / "05_3_authority_results.jsonl"
        ),

        "candidates": (
            OUT
            / "05_3_authority_candidates.jsonl"
        ),

        "review": (
            OUT
            / "05_3_authority_review.jsonl"
        ),

        "manual": (
            OUT
            / "05_3_manual_review.csv"
        ),

        "summary": (
            OUT
            / "05_3_summary.json"
        ),
    }

    test_mode = bool(
        args.author
        or args.limit is not None
    )

    if not test_mode:

        if (
            not args.overwrite

            and any(
                path.exists()
                for path in output_paths.values()
            )
        ):

            raise FileExistsError(
                "05_3 outputs already exist. "
                "Back up manual review files "
                "and use --overwrite."
            )

    groups = read_jsonl(
        input_path
    )

    original_group_count = len(
        groups
    )

    original_row_count = sum(
        len(group["unresolved_books"])
        for group in groups
    )

    if args.author:

        requested = {
            name_key(name)
            for name in args.author
        }

        groups = [
            group
            for group in groups

            if requested & {
                name_key(name)

                for name in [
                    group["author_name"],
                    *group.get(
                        "name_variants",
                        [],
                    ),
                ]
            }
        ]

    if args.limit is not None:

        groups = groups[:args.limit]

    if not groups:

        raise RuntimeError(
            "No matching input groups"
        )

    client = m.Client(
        delay=args.delay
    )

    cache = QueryCache(
        OUT / "05_3_query_cache.jsonl",
        client,
    )

    (
        people,
        kac_index,
        isni_index,
    ) = load_existing_identifiers()

    print(
        f"[INFO] Existing book authors: "
        f"{len(people)}"
    )

    print(
        f"[INFO] Input groups: "
        f"{len(groups)}"
    )

    print(
        f"[INFO] Input book-author rows: "
        f"{sum(len(g['unresolved_books']) for g in groups)}"
    )

    print(
        f"[INFO] Test mode: {test_mode}"
    )

    results = []
    candidates = []
    review = []

    counts = Counter()

    accepted_statuses = {
        "reuse_existing_authority_candidate",
        "new_authority_candidate",
    }

    for i, group in enumerate(
        groups,
        1,
    ):

        group_id = group[
            "group_id"
        ]

        author_name = group[
            "author_name"
        ]

        names = sorted({
            author_name,

            *group.get(
                "name_variants",
                [],
            ),
        })

        print(
            f"\n[GROUP {i}/{len(groups)}] "
            f"{group_id} | {author_name}"
        )

        (
            name_authors,
            search_names,
            name_complete,
        ) = search_author_names(
            cache,
            names,
        )

        print(
            f"[INFO] Name search variants: "
            f"{search_names}"
        )

        print(
            f"[INFO] Name authority candidates: "
            f"{len(name_authors)}"
        )

        for row in group[
            "unresolved_books"
        ]:

            result = process_row(
                row=row,

                cache=cache,

                name_authors=name_authors,

                name_search_complete=name_complete,

                people=people,

                kac_index=kac_index,

                isni_index=isni_index,
            )

            result["group_id"] = group_id

            result[
                "author_name_search_variants"
            ] = search_names

            results.append(result)

            status = result[
                "authority_status"
            ]

            counts[status] += 1

            if status in accepted_statuses:

                candidates.append(
                    result
                )

            else:

                review.append(
                    result
                )

            selected = result.get(
                "selected_authority"
            ) or {}

            print(
                f"[{status}] "
                f"{row['title']} | "
                f"{row['author_name']} | "
                f"KAC={selected.get('kac_ids', [])} | "
                f"ISNI={selected.get('isni', [])} | "
                f"entity_id={selected.get('existing_entity_id')}"
            )

    # -----------------------------------------------------
    # Summary
    # -----------------------------------------------------

    review_groups = {
        row["group_id"]
        for row in review
    }

    candidate_groups = {
        row["group_id"]
        for row in candidates
    }

    summary = {
        "version": VERSION,

        "test_mode": test_mode,

        "original_input_groups": (
            original_group_count
        ),

        "original_input_rows": (
            original_row_count
        ),

        "processed_groups": len(
            groups
        ),

        "processed_rows": len(
            results
        ),

        "existing_book_authors": len(
            people
        ),

        "status_counts": dict(
            counts
        ),

        "reuse_existing_candidates": counts[
            "reuse_existing_authority_candidate"
        ],

        "new_authority_candidates": counts[
            "new_authority_candidate"
        ],

        "total_authority_candidates": len(
            candidates
        ),

        "authority_candidate_groups": len(
            candidate_groups
        ),

        "remaining_manual_rows": len(
            review
        ),

        "remaining_manual_groups": len(
            review_groups
        ),

        "candidate_rate_pct": (
            round(
                len(candidates)
                / len(results) * 100,
                2,
            )
            if results
            else 0
        ),

        "cache_hits": cache.hits,

        "api_calls": cache.calls,

        "query_errors": cache.errors,

        "database_changed": False,

        "final_exclusions": 0,
    }

    # Test runs must not overwrite
    # production output files.

    if test_mode:

        test_path = (
            OUT
            / "05_3_test_results.jsonl"
        )

        write_jsonl(
            test_path,
            results,
        )

        summary["test_output"] = str(
            test_path
        )

    else:

        write_jsonl(
            output_paths["results"],
            results,
        )

        write_jsonl(
            output_paths["candidates"],
            candidates,
        )

        write_jsonl(
            output_paths["review"],
            review,
        )

        write_review_csv(
            output_paths["manual"],
            review,
        )

        summary["outputs"] = [
            str(path)
            for path in output_paths.values()
        ]

        write_json(
            output_paths["summary"],
            summary,
        )

    print("\n=== SUMMARY ===")

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()